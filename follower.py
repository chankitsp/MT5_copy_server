import time
import json
import os
import socket
from typing import Dict, Any, Optional

import requests
import MetaTrader5 as mt5

API_BASE = "http://192.168.0.103:5990"
API_TOKEN = "change-me"
FOLLOWER_ID = os.getenv("FOLLOWER_ID") or socket.gethostname()

POLL_SECONDS = 1.0
DEVIATION = 20
MAGIC = 990001

SYMBOL_MAP = {
    # "BTCUSD": "BTCUSDm"
}

LOT_MULTIPLIER = 1.0
STATE_FILE = "follower_state.json"


def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": str(event["event_id"]),
        "action": str(event["action"]).strip().lower(),
        "symbol": str(event["symbol"]).strip(),
        "side": str(event["side"]).strip().lower(),
        "volume": float(event["volume"]),
        "magic": int(event["magic"]),
        "timestamp": int(event["timestamp"]),
        "sl": float(event.get("sl", 0) or 0),
        "tp": float(event.get("tp", 0) or 0),
    }


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {"processed_event_ids": []}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    if "processed_event_ids" not in state:
        state["processed_event_ids"] = []

    return state


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def ensure_mt5() -> None:
    if not mt5.initialize():
        raise RuntimeError(f"mt5.initialize() failed: {mt5.last_error()}")

    account = mt5.account_info()
    if account is None:
        raise RuntimeError("MT5 initialized but account_info() is None")

    print(f"Connected MT5 account: {account.login}")


def resolve_symbol(master_symbol: str) -> str:
    return SYMBOL_MAP.get(master_symbol, master_symbol)


def ensure_symbol(symbol: str) -> bool:
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"Symbol not found: {symbol}")
        return False

    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            print(f"Failed to select symbol: {symbol}")
            return False

    return True


def normalize_volume(symbol: str, volume: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"symbol_info is None for {symbol}")

    min_vol = info.volume_min
    max_vol = info.volume_max
    step = info.volume_step

    volume = max(min_vol, min(max_vol, volume))
    steps = round(volume / step)
    normalized = round(steps * step, 8)
    return normalized


def get_open_events() -> list:
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    params = {"follower_id": FOLLOWER_ID}
    r = requests.get(f"{API_BASE}/pull", headers=headers, params=params, timeout=10)
    r.raise_for_status()
    return r.json()["events"]


def ack_event(event_id: str, status: str, detail: str = "") -> None:
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    payload = {
        "event_id": event_id,
        "follower_id": FOLLOWER_ID,
        "status": status,
        "detail": detail,
    }
    r = requests.post(f"{API_BASE}/ack", headers=headers, json=payload, timeout=10)
    r.raise_for_status()


def is_stale_open_event(event: Dict[str, Any], started_at: int) -> bool:
    return event["action"] == "open" and event["timestamp"] < started_at


def send_market_order(
    symbol: str,
    side: str,
    volume: float,
    sl: float,
    tp: float,
    comment: str
) -> Optional[int]:
    if not ensure_symbol(symbol):
        return None

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}")

    order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
    price = tick.ask if side == "buy" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": normalize_volume(symbol, volume),
        "type": order_type,
        "price": price,
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": comment[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    if sl and sl > 0:
        request["sl"] = sl
    if tp and tp > 0:
        request["tp"] = tp

    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError("order_send returned None")

    print("OPEN result:", result)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"OPEN failed retcode={result.retcode}, comment={result.comment}")

    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None

    latest = sorted(positions, key=lambda x: x.time, reverse=True)[0]
    return int(latest.ticket)


def find_position_by_ticket(ticket: int):
    positions = mt5.positions_get(ticket=ticket)
    if positions:
        return positions[0]
    return None


def close_position_by_ticket(ticket: int) -> bool:
    pos = find_position_by_ticket(ticket)
    if pos is None:
        print(f"No open follower position for ticket={ticket}")
        return False

    symbol = pos.symbol

    if not ensure_symbol(symbol):
        return False

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}")

    if pos.type == mt5.POSITION_TYPE_BUY:
        close_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": pos.volume,
        "type": close_type,
        "position": pos.ticket,
        "price": price,
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": "copier-close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError("close order_send returned None")

    print("CLOSE result:", result)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"CLOSE failed retcode={result.retcode}, comment={result.comment}")

    return True


def find_position_to_close(symbol: str, side: str):
    if not ensure_symbol(symbol):
        return None

    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None

    target_type = mt5.POSITION_TYPE_BUY if side == "buy" else mt5.POSITION_TYPE_SELL
    matching_positions = [pos for pos in positions if pos.type == target_type]
    if not matching_positions:
        return None

    return sorted(matching_positions, key=lambda x: x.time, reverse=True)[0]


def process_open(event: Dict[str, Any], state: Dict[str, Any]) -> None:
    symbol = resolve_symbol(event["symbol"])
    side = event["side"]
    volume = float(event["volume"]) * LOT_MULTIPLIER
    sl = float(event.get("sl", 0) or 0)
    tp = float(event.get("tp", 0) or 0)
    comment = f"event:{event['event_id']}"

    follower_ticket = send_market_order(symbol, side, volume, sl, tp, comment)
    if follower_ticket is None:
        raise RuntimeError("Failed to resolve follower ticket after open")

    print(f"OPENED follower_ticket={follower_ticket} for event_id={event['event_id']}")


def process_close(event: Dict[str, Any], state: Dict[str, Any]) -> str:
    symbol = resolve_symbol(event["symbol"])
    side = event["side"]
    position = find_position_to_close(symbol, side)

    if position is None:
        print(f"[SKIP CLOSE] No open follower position for symbol={symbol} side={side}")
        return "skipped:not_found"

    ok = close_position_by_ticket(int(position.ticket))
    if not ok:
        print(f"[SKIP CLOSE] No open follower position for ticket={position.ticket}")
        return "skipped:not_found"

    print(f"CLOSED follower_ticket={position.ticket} for event_id={event['event_id']}")
    return "closed"


def process_event(event: Dict[str, Any], state: Dict[str, Any], started_at: int) -> None:
    event = normalize_event(event)
    event_id = event["event_id"]
    action = event["action"]

    if event_id in state["processed_event_ids"]:
        print(f"Skip duplicate event {event_id}")
        ack_event(event_id, "done", "duplicate_already_processed")
        return

    if is_stale_open_event(event, started_at):
        print(
            f"Ignore stale open event {event_id}: "
            f"event_timestamp={event['timestamp']} follower_started_at={started_at}"
        )
        state["processed_event_ids"].append(event_id)
        save_state(state)
        ack_event(event_id, "ignored", "stale_open_before_follower_start")
        return

    if action == "open":
        process_open(event, state)
        state["processed_event_ids"].append(event_id)
        save_state(state)
        ack_event(event_id, "done", "opened")

    elif action == "close":
        close_result = process_close(event, state)
        state["processed_event_ids"].append(event_id)
        save_state(state)

        if close_result == "closed":
            ack_event(event_id, "done", "closed")
        elif close_result == "skipped:not_found":
            ack_event(event_id, "done", "skipped_close_position_not_found")
        else:
            ack_event(event_id, "done", close_result)

    else:
        state["processed_event_ids"].append(event_id)
        save_state(state)
        ack_event(event_id, "ignored", f"unsupported action={action}")


def main() -> None:
    ensure_mt5()
    state = load_state()
    started_at = int(time.time())
    print(f"Follower started_at={started_at} follower_id={FOLLOWER_ID}")

    while True:
        try:
            events = get_open_events()
            for event in events:
                try:
                    process_event(event, state, started_at)
                except Exception as e:
                    print("process_event error:", e)
                    try:
                        ack_event(event["event_id"], "error", str(e))
                    except Exception as ack_err:
                        print("ack_event error:", ack_err)
        except Exception as e:
            print("poll error:", e)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

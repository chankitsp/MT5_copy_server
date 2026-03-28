from typing import Any, Dict, List

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

app = FastAPI(title="MT5 Copier API")

API_TOKEN = "change-me"

EVENTS: List[Dict[str, Any]] = []
ACKS: Dict[str, Dict[str, Dict[str, Any]]] = {}


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    action: str
    symbol: str
    side: str
    volume: float
    magic: int | str
    timestamp: int | str
    sl: float = 0
    tp: float = 0


class SocketAckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    event_id: str
    status: str
    detail: str = ""


def check_auth(authorization: str) -> None:
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def is_terminal_status(status_value: str) -> bool:
    return status_value in ("done", "ignored")


def normalize_event(data: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(data)
    normalized["magic"] = int(normalized["magic"])
    normalized["timestamp"] = int(normalized["timestamp"])
    normalized["symbol"] = str(normalized["symbol"]).strip()
    normalized["side"] = str(normalized["side"]).strip().lower()
    return normalized


def get_follower_ack(event_id: str, follower_id: str) -> Dict[str, Any] | None:
    return ACKS.get(event_id, {}).get(follower_id)


def is_acknowledged_for_follower(event_id: str, follower_id: str) -> bool:
    follower_ack = get_follower_ack(event_id, follower_id)
    return follower_ack is not None and is_terminal_status(follower_ack["status"])


def pending_events_for_follower(follower_id: str) -> List[Dict[str, Any]]:
    pending: List[Dict[str, Any]] = []
    for event in EVENTS:
        if is_acknowledged_for_follower(event["event_id"], follower_id):
            continue
        pending.append(event)
    return pending


def record_ack(event_id: str, follower_id: str, status_value: str, detail: str) -> None:
    ACKS.setdefault(event_id, {})
    ACKS[event_id][follower_id] = {
        "status": status_value,
        "detail": detail,
    }
    print("ACK RECEIVED:", ACKS[event_id][follower_id], "for", event_id, "from", follower_id)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, follower_id: str, websocket: WebSocket) -> None:
        await websocket.accept()

        previous = self.active_connections.get(follower_id)
        if previous is not None and previous is not websocket:
            await previous.close(code=status.WS_1000_NORMAL_CLOSURE)

        self.active_connections[follower_id] = websocket

    def disconnect(self, follower_id: str, websocket: WebSocket | None = None) -> None:
        current = self.active_connections.get(follower_id)
        if current is None:
            return

        if websocket is not None and current is not websocket:
            return

        self.active_connections.pop(follower_id, None)

    def follower_ids(self) -> List[str]:
        return sorted(self.active_connections)

    async def send_json(self, follower_id: str, payload: Dict[str, Any]) -> bool:
        websocket = self.active_connections.get(follower_id)
        if websocket is None:
            return False

        try:
            await websocket.send_json(payload)
            return True
        except Exception as exc:
            print(f"websocket send error follower_id={follower_id}: {exc}")
            self.disconnect(follower_id, websocket)
            return False

    async def send_event(self, follower_id: str, event: Dict[str, Any]) -> bool:
        return await self.send_json(follower_id, {"type": "event", "event": event})

    async def broadcast_event(self, event: Dict[str, Any]) -> List[str]:
        delivered: List[str] = []
        for follower_id in list(self.active_connections):
            ok = await self.send_event(follower_id, event)
            if ok:
                delivered.append(follower_id)
        return delivered


manager = ConnectionManager()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    body_text = body.decode("utf-8", errors="ignore")
    print("VALIDATION ERROR BODY:", body_text)
    print("VALIDATION ERROR DETAILS:", exc.errors())
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
            "body": body_text,
        },
    )


@app.get("/")
def root():
    return {"ok": True, "service": "mt5-copier-api"}


@app.get("/status")
def status_view():
    total_acks = sum(len(follower_acks) for follower_acks in ACKS.values())
    pending_deliveries = sum(
        len(pending_events_for_follower(follower_id))
        for follower_id in manager.follower_ids()
    )

    return {
        "ok": True,
        "total_events": len(EVENTS),
        "total_acks": total_acks,
        "active_followers": len(manager.follower_ids()),
        "follower_ids": manager.follower_ids(),
        "pending_deliveries": pending_deliveries,
        "queued_events_without_ack": sum(1 for event in EVENTS if event["event_id"] not in ACKS),
    }


@app.get("/admin/events")
def admin_events(authorization: str = Header(default="")):
    check_auth(authorization)

    snapshot = []
    active_followers = manager.follower_ids()
    for event in EVENTS:
        event_id = event["event_id"]
        pending_followers = [
            follower_id
            for follower_id in active_followers
            if not is_acknowledged_for_follower(event_id, follower_id)
        ]
        snapshot.append(
            {
                "event": event,
                "acks": ACKS.get(event_id, {}),
                "pending_followers": pending_followers,
            }
        )

    return {"ok": True, "events": snapshot}


@app.get("/admin/followers")
def admin_followers(authorization: str = Header(default="")):
    check_auth(authorization)
    return {
        "ok": True,
        "followers": manager.follower_ids(),
    }


@app.post("/events")
async def push_event(event: EventIn, authorization: str = Header(default="")):
    check_auth(authorization)

    data = normalize_event(event.model_dump())

    if data["action"] not in ("open", "close"):
        raise HTTPException(status_code=400, detail="invalid action")

    if data["side"] not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="invalid side")

    for current in EVENTS:
        if current["event_id"] == data["event_id"]:
            return {"ok": True, "duplicate": True}

    EVENTS.append(data)
    delivered_to = await manager.broadcast_event(data)
    print("EVENT RECEIVED:", data, "delivered_to=", delivered_to)

    return {
        "ok": True,
        "queued": True,
        "delivered_to": delivered_to,
    }


@app.websocket("/ws/{follower_id}")
async def websocket_endpoint(websocket: WebSocket, follower_id: str):
    token = websocket.query_params.get("token", "")
    if token != API_TOKEN:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(follower_id, websocket)
    print(f"FOLLOWER CONNECTED follower_id={follower_id}")

    try:
        await manager.send_json(
            follower_id,
            {
                "type": "welcome",
                "follower_id": follower_id,
            },
        )

        for event in pending_events_for_follower(follower_id):
            await manager.send_event(follower_id, event)

        while True:
            payload = await websocket.receive_json()
            message_type = payload.get("type")

            if message_type != "ack":
                await manager.send_json(
                    follower_id,
                    {
                        "type": "error",
                        "detail": f"unsupported message type={message_type}",
                    },
                )
                continue

            ack = SocketAckIn(**payload)
            record_ack(ack.event_id, follower_id, ack.status, ack.detail)

    except WebSocketDisconnect:
        print(f"FOLLOWER DISCONNECTED follower_id={follower_id}")
        manager.disconnect(follower_id, websocket)
    except Exception as exc:
        print(f"FOLLOWER SOCKET ERROR follower_id={follower_id}: {exc}")
        manager.disconnect(follower_id, websocket)
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass

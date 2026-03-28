from typing import Dict, Any, List
from fastapi import FastAPI, Header, HTTPException, Query, Request
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


class AckIn(BaseModel):
    event_id: str
    follower_id: str
    status: str
    detail: str = ""


def check_auth(authorization: str) -> None:
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


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
            "body": body_text
        },
    )


@app.get("/")
def root():
    return {"ok": True, "service": "mt5-copier-api"}


@app.get("/status")
def status():
    pending = 0
    total_acks = 0
    for e in EVENTS:
        follower_acks = ACKS.get(e["event_id"], {})
        total_acks += len(follower_acks)
        if not follower_acks:
            pending += 1

    return {
        "ok": True,
        "total_events": len(EVENTS),
        "total_acks": total_acks,
        "pending_events": pending,
    }


@app.post("/events")
def push_event(event: EventIn, authorization: str = Header(default="")):
    check_auth(authorization)

    data = event.model_dump()

    data["magic"] = int(data["magic"])
    data["timestamp"] = int(data["timestamp"])
    data["symbol"] = str(data["symbol"]).strip()
    data["side"] = str(data["side"]).strip().lower()

    if data["action"] not in ("open", "close"):
        raise HTTPException(status_code=400, detail="invalid action")

    if data["side"] not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="invalid side")

    for e in EVENTS:
        if e["event_id"] == data["event_id"]:
            return {"ok": True, "duplicate": True}

    EVENTS.append(data)
    print("EVENT RECEIVED:", data)
    return {"ok": True, "queued": True}


@app.get("/pull")
def pull_events(
    authorization: str = Header(default=""),
    follower_id: str = Query(...),
):
    check_auth(authorization)

    pending = []
    for e in EVENTS:
        follower_ack = ACKS.get(e["event_id"], {}).get(follower_id)
        if follower_ack and follower_ack["status"] in ("done", "ignored"):
            continue
        pending.append(e)

    return {"ok": True, "events": pending}


@app.post("/ack")
def ack_event(data: AckIn, authorization: str = Header(default="")):
    check_auth(authorization)

    ACKS.setdefault(data.event_id, {})
    ACKS[data.event_id][data.follower_id] = {
        "status": data.status,
        "detail": data.detail,
    }
    print("ACK RECEIVED:", ACKS[data.event_id][data.follower_id], "for", data.event_id, "from", data.follower_id)
    return {"ok": True}

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db
from app.routers.auth import router as auth_router
from app.routers.messages import router as messages_router
from app.routers.profiles import router as profiles_router
from app.realtime import manager
from app.security import decode_token
from app.db import SessionLocal
from app.models import Session, User

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(messages_router)
app.include_router(profiles_router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token", "")
    try:
        payload = decode_token(token, "access")
        user_id = int(payload["sub"])
        session_id = int(payload["sid"])
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        user = await db.get(User, user_id)
        if (
            session is None
            or user is None
            or session.user_id != user_id
            or session.revoked_at is not None
        ):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await manager.connect(user_id, websocket)
    await manager.send_user(user_id, {"type": "connection", "status": "connected"})
    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        await manager.disconnect(user_id, websocket)
    except Exception:
        await manager.disconnect(user_id, websocket)


media_dir = Path(get_settings().media_dir)
media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "belgram-api", "version": settings.app_version}


@app.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}

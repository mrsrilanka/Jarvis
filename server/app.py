"""Jarvis server: serves the wall dashboard and answers questions.

Endpoints
  GET  /                 the dashboard
  GET  /api/dashboard    weather, commute and news for the cards
  POST /api/ask          {"text": "..."} -> {"answer": "..."}   (used by the voice loop)
  POST /api/state        {"state": "listening|thinking|speaking|idle", "text": "..."}
  WS   /ws               live orb state + conversation pushed to the dashboard
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from data import DataHub
from llm import Brain

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("jarvis")

CONFIG_PATH = Path(os.getenv("JARVIS_CONFIG", ROOT / "config.yaml"))
if not CONFIG_PATH.exists():
    CONFIG_PATH = ROOT / "config.example.yaml"
    log.warning("config.yaml not found, using config.example.yaml")
CFG = yaml.safe_load(CONFIG_PATH.read_text())

hub = DataHub(CFG)
brain = Brain(CFG, hub)
app = FastAPI(title="Jarvis")
clients: set[WebSocket] = set()
ask_lock = asyncio.Lock()


async def broadcast(msg: dict):
    dead = []
    for ws in clients:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


class Ask(BaseModel):
    text: str


class State(BaseModel):
    state: str
    text: str = ""


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/healthz")
def health():
    return {"ok": True, "provider": brain.provider}


@app.get("/api/dashboard")
async def dashboard():
    return JSONResponse(await run_in_threadpool(hub.dashboard))


@app.post("/api/ask")
async def ask(q: Ask):
    text = q.text.strip()
    if not text:
        return {"answer": ""}
    async with ask_lock:  # one question at a time
        await broadcast({"type": "heard", "text": text})
        await broadcast({"type": "state", "state": "thinking"})
        answer = await run_in_threadpool(brain.ask, text)
        await broadcast({"type": "answer", "text": answer})
    log.info("Q: %s | A: %s", text, answer)
    return {"answer": answer}


@app.post("/api/state")
async def set_state(s: State):
    await broadcast({"type": "state", "state": s.state, "text": s.text})
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive pings from the page
    except WebSocketDisconnect:
        clients.discard(ws)


@app.on_event("startup")
async def warm_cache():
    # Pre-fetch so the dashboard is filled the moment the screen loads,
    # and refresh in the background so data is always fresh.
    async def loop():
        while True:
            await run_in_threadpool(hub.dashboard)
            await asyncio.sleep(hub.ttl)
    asyncio.create_task(loop())

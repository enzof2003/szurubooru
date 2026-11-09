"""HTTP service: POST /translate, GET /health.

Run:  .venv\\Scripts\\python app.py        (binds HOST:PORT from .env, default 127.0.0.1:8765)

Requests are processed strictly one at a time (FIFO); up to MAX_QUEUE more wait, the rest get 503.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import config

import requests
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from pipeline import LlmError, Pipeline, clean_context_tags, load_image

log = logging.getLogger("mtl")
settings = config.load()
state: dict = {"waiting": 0, "warming": False}
work_lock = asyncio.Lock()  # FIFO: one page in flight at a time


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.api_key or len(settings.api_key) < 16:
        raise RuntimeError("Set API_KEY in .env (at least 16 characters)")
    if settings.nice and hasattr(os, "nice"):
        os.nice(settings.nice)  # lower CPU priority than szurubooru; inherited by the OCR server
    state["pipeline"] = await run_in_threadpool(Pipeline, settings)
    log.info("ready: %d threads, OCR server %s", settings.threads, settings.ocr_server)
    state["warming"] = settings.warmup  # set before the task runs: /health never says "ok" too early
    warm = asyncio.create_task(warmup()) if settings.warmup else None
    yield
    if warm:
        warm.cancel()
    state["pipeline"].close()
    state.pop("pipeline", None)


async def warmup() -> None:
    """Runs once after startup, holding the work lock: real requests queue behind it."""
    try:
        async with work_lock:
            t = await run_in_threadpool(state["pipeline"].warmup)
        log.info("warm-up done: %s", t)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("warm-up failed (the first request will be slower)")
    finally:
        state["warming"] = False


app = FastAPI(title="manga-translate", version="2.0", lifespan=lifespan, docs_url=None, redoc_url=None)


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), settings.api_key.encode()):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


def _max_bytes() -> int:
    return int(settings.max_image_mb * 1024 * 1024)


def fetch_url(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "image_url must be an http(s) URL")
    host = parsed.hostname.lower()
    if settings.allowed_url_hosts and host not in settings.allowed_url_hosts:
        raise HTTPException(403, f"host {host!r} is not in ALLOWED_URL_HOSTS")
    if not settings.allowed_url_hosts:
        # No allowlist: refuse private/loopback targets to avoid SSRF into the host's network.
        try:
            addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
        except socket.gaierror:
            raise HTTPException(400, f"cannot resolve {host!r}")
        for a in addrs:
            ip = ipaddress.ip_address(a.split("%")[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise HTTPException(403, f"{host!r} resolves to a non-public address; add it to ALLOWED_URL_HOSTS")
    try:
        with requests.get(url, stream=True, timeout=(10, 60), allow_redirects=False) as r:
            if r.status_code != 200:
                raise HTTPException(400, f"image_url returned HTTP {r.status_code}")
            buf = bytearray()
            for chunk in r.iter_content(64 * 1024):
                buf.extend(chunk)
                if len(buf) > _max_bytes():
                    raise HTTPException(413, "image too large")
            return bytes(buf)
    except requests.RequestException as e:
        raise HTTPException(400, f"could not fetch image_url: {e}")


def _context_tags(raw) -> dict[str, list[str]]:
    try:
        return clean_context_tags(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/health")
def health():
    p = state.get("pipeline")
    ocr_ok = p is not None and (p.ocr_server is None or p.ocr_server.alive())
    status = "loading" if not p else "degraded" if not ocr_ok else "warming" if state["warming"] else "ok"
    return {"status": status,
            "busy": work_lock.locked(), "queued": state["waiting"]}


@app.post("/translate", dependencies=[Depends(require_key)])
async def translate(request: Request, translate: bool = Query(True, description="false = detection + OCR only")):
    ctype = request.headers.get("content-type", "")
    # Optional post tags (image context for the LLM): {"character": [...], "series": [...], "artist": [...]}
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        raw = form.get("context_tags")
        try:
            context_tags = _context_tags(json.loads(raw) if isinstance(raw, str) and raw else None)
        except ValueError:
            raise HTTPException(400, "context_tags must be a JSON object")
        upload = form.get("image") or form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(400, "multipart field 'image' is required")
        data = await upload.read()
    elif ctype.startswith("application/json"):
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "invalid JSON body")
        url = payload.get("image_url") if isinstance(payload, dict) else None
        if not url:
            raise HTTPException(400, "JSON body must contain image_url")
        context_tags = _context_tags(payload.get("context_tags"))  # checked before the (slow) fetch
        data = await run_in_threadpool(fetch_url, url)
    else:
        raise HTTPException(415, "send multipart/form-data (field 'image') or JSON {\"image_url\": ...}")

    if len(data) > _max_bytes():
        raise HTTPException(413, "image too large")
    try:
        img = load_image(data)
    except Exception as e:  # PIL raises many types
        raise HTTPException(400, f"not a readable image: {e}")

    if work_lock.locked() and state["waiting"] >= settings.max_queue:
        raise HTTPException(503, "queue full, retry later", headers={"Retry-After": "30"})
    state["waiting"] += 1
    try:
        await work_lock.acquire()  # cancelled while waiting (client gone) -> counter still restored
    finally:
        state["waiting"] -= 1
    try:
        return await run_in_threadpool(state["pipeline"].run, img, translate, context_tags)
    except LlmError as e:
        raise HTTPException(502, str(e))
    finally:
        work_lock.release()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info", workers=1)

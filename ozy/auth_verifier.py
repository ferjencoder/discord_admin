"""Private, stateless password work for Cloudflare. Never a public login API."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from pathlib import Path

from aiohttp import web

PATH = "/internal/auth/v1"
MAX_BODY = 4096
log = logging.getLogger(__name__)


def memory_status():
    """Linux process counters; Render does not expose free-plan memory charts."""
    try:
        values = {}
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                key, value, _unit = line.split()
                values[key.rstrip(":")] = int(value) * 1024
        return values
    except (OSError, ValueError):
        return {}


def memory_available():
    # Leave room for scrypt's 192 MiB allocation ceiling plus 64 MiB for the
    # Discord bot and normal transient work, within the verified 512 MiB plan.
    current = memory_status().get("VmRSS")
    if current is None:
        return not bool(os.environ.get("RENDER"))
    return current <= 256 * 1024 * 1024


def validate(value):
    if not isinstance(value, dict) or set(value) - {"operation", "kind", "password", "record", "subject"}:
        raise ValueError("invalid request")
    operation, kind = value.get("operation"), value.get("kind")
    password, subject = value.get("password"), value.get("subject")
    if not isinstance(operation, str) or not isinstance(kind, str) or operation not in {"verify", "hash"} or kind not in {"personal", "admin"}:
        raise ValueError("invalid operation")
    if not isinstance(password, str) or not 1 <= len(password.encode("utf-8")) <= 512:
        raise ValueError("invalid password")
    if not isinstance(subject, str) or not re.fullmatch(r"[a-f0-9]{64}", subject):
        raise ValueError("invalid subject")
    if operation == "hash":
        if kind == "personal" and not re.fullmatch(r"[0-9]{6,12}", password):
            raise ValueError("invalid PIN")
        if kind == "admin" and not 12 <= len(password) <= 128:
            raise ValueError("invalid password")
    else:
        record = value.get("record")
        if not isinstance(record, dict) or record.get("algorithm") != "scrypt":
            raise ValueError("invalid record")
        size = 64 if kind == "admin" else 32
        if not isinstance(record.get("salt"), str) or not re.fullmatch(r"[a-f0-9]{32}", record["salt"]):
            raise ValueError("invalid salt")
        if not isinstance(record.get("hash"), str) or not re.fullmatch(r"[a-f0-9]{%d}" % (size * 2), record["hash"]):
            raise ValueError("invalid hash")
    return value


def calculate(value):
    # Salt is UTF-8 text, matching Node's scrypt(password, saltString), not hex bytes.
    kind = value["kind"]
    salt = value["record"]["salt"] if value["operation"] == "verify" else secrets.token_hex(16)
    derived = hashlib.scrypt(
        value["password"].encode("utf-8"), salt=salt.encode("ascii"),
        n=131072 if kind == "admin" else 16384, r=8, p=1,
        maxmem=192 * 1024 * 1024, dklen=64 if kind == "admin" else 32,
    ).hex()
    if value["operation"] == "verify":
        return {"matched": hmac.compare_digest(derived, value["record"]["hash"])}
    return {"record": {"algorithm": "scrypt", "salt": salt, "hash": derived}}


class Verifier:
    def __init__(self, token, *, clock=time.monotonic, compute=calculate, can_compute=memory_available):
        self.token = token
        self.clock = clock
        self.compute = compute
        self.can_compute = can_compute
        self.attempts = deque()
        self.active = None

    def limited(self, subject):
        now = self.clock()
        while self.attempts and self.attempts[0][0] <= now - 60:
            self.attempts.popleft()
        if len(self.attempts) >= 60 or sum(s == subject for _, s in self.attempts) >= 10:
            return True
        self.attempts.append((now, subject))
        return False

    async def handle(self, request):
        def reply(body, status=200):
            return web.json_response(body, status=status, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

        if not self.token:
            return reply({"error": "unavailable"}, 503)
        provided = request.headers.get("Authorization", "").encode("utf-8")
        if not hmac.compare_digest(provided, ("Bearer " + self.token).encode("utf-8")):
            return reply({"error": "unauthorized"}, 401)
        if request.content_type != "application/json":
            return reply({"error": "invalid_request"}, 400)
        # Bound chunked bodies too. Never log payloads, hashes, tokens, or errors.
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.content.iter_chunked(1024):
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        return reply({"error": "too_large"}, 413)
            payload = json.loads(body)
            if payload == {"operation": "status"}:
                return reply({"memory": memory_status(), "password_work_available": self.can_compute(), "busy": self.active is not None and not self.active.done()})
            value = validate(payload)
        except (ValueError, UnicodeError, TimeoutError):
            return reply({"error": "invalid_request"}, 400)
        if self.limited(value["subject"]):
            return reply({"error": "rate_limited"}, 429)
        if self.active is not None and not self.active.done():
            return reply({"error": "busy"}, 503)
        if not self.can_compute():
            return reply({"error": "busy"}, 503)
        # No await between checking and reserving the single slot. Shield keeps it
        # occupied if the HTTP caller disconnects while the native work continues.
        def run_compute():
            started = time.monotonic()
            try:
                return self.compute(value)
            finally:
                stats = memory_status()
                log.info("Password work: elapsed_ms=%d rss_mib=%s process_peak_mib=%s",
                         int((time.monotonic() - started) * 1000),
                         round(stats["VmRSS"] / 1048576, 1) if "VmRSS" in stats else None,
                         round(stats["VmHWM"] / 1048576, 1) if "VmHWM" in stats else None)
        task = asyncio.create_task(asyncio.to_thread(run_compute))
        self.active = task
        task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
        try:
            return reply(await asyncio.shield(task))
        except asyncio.CancelledError:
            raise
        except Exception:
            return reply({"error": "unavailable"}, 503)


def install_auth_verifier(app, token=None):
    token = os.environ.get("OZY_AUTH_SERVICE_TOKEN", "") if token is None else token
    # Absent configuration leaves the route disabled and the Discord bot usable.
    if token and not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ValueError("OZY_AUTH_SERVICE_TOKEN must be 64 lowercase hex characters")
    verifier = Verifier(token)
    app.router.add_post(PATH, verifier.handle)
    return verifier

import asyncio
import json
import subprocess
import threading

from aiohttp import web, ClientSession
from ozy.auth_verifier import Verifier, PATH, install_auth_verifier, calculate, validate

TOKEN = "a" * 64
BASE = {"kind": "personal", "operation": "hash", "password": "783201", "subject": "b" * 64}


def test_node_compatibility():
    # Generate fixtures in Node independently; catches salt encoding and settings drift.
    script = """const c=require('node:crypto'),salt='0123456789abcdef0123456789abcdef',p='Dummy-password-783201';
    console.log(JSON.stringify(['personal','admin'].map(kind=>({kind,operation:'verify',password:p,subject:'b'.repeat(64),record:{algorithm:'scrypt',salt,hash:c.scryptSync(p,salt,kind==='admin'?64:32,kind==='admin'?{N:131072,r:8,p:1,maxmem:192*1024*1024}:{}).toString('hex')}}))))"""
    values = json.loads(subprocess.check_output(["node", "-e", script], text=True))
    for value in values:
        assert calculate(validate(value)) == {"matched": True}
        assert calculate(validate({**value, "password": "wrong"})) == {"matched": False}
    created = calculate(validate(BASE))
    assert calculate(validate({**BASE, "operation": "verify", **created})) == {"matched": True}


def test_http_boundaries_and_limits():
    async def run():
        app = web.Application()
        verifier = install_auth_verifier(app, TOKEN)
        verifier.compute = lambda value: {"matched": True}
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        url = f"http://127.0.0.1:{runner.addresses[0][1]}{PATH}"
        try:
            async with ClientSession() as client:
                async with client.post(url, json=BASE) as response:
                    assert response.status == 401
                headers = {"Authorization": "Bearer " + TOKEN}
                async with client.post(url, headers=headers, json={**BASE, "kind": "evil"}) as response:
                    assert response.status == 400
                async with client.post(url, headers=headers, data=b"x" * 5000) as response:
                    assert response.status == 400
                async with client.post(url, headers={**headers, "Content-Type": "application/json"}, data=b"x" * 5000) as response:
                    assert response.status == 413
                for _ in range(10):
                    async with client.post(url, headers=headers, json=BASE) as response:
                        assert response.status == 200
                        assert response.headers["Cache-Control"] == "no-store"
                async with client.post(url, headers=headers, json=BASE) as response:
                    assert response.status == 429
                verifier.token = ""
                async with client.post(url, headers=headers, json=BASE) as response:
                    assert response.status == 503
        finally:
            await runner.cleanup()
    asyncio.run(run())


def test_busy_does_not_block_event_loop():
    async def run():
        started, release = threading.Event(), threading.Event()
        def compute(_):
            started.set()
            release.wait(5)
            return {"matched": False}
        verifier = Verifier(TOKEN, compute=compute)
        app = web.Application()
        app.router.add_post(PATH, verifier.handle)
        async def health(_):
            return web.json_response({'ok': True})
        app.router.add_get('/healthz', health)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        origin = f"http://127.0.0.1:{runner.addresses[0][1]}"
        try:
            async with ClientSession(headers={"Authorization": "Bearer " + TOKEN}) as client:
                first = asyncio.create_task(client.post(origin + PATH, json=BASE))
                assert await asyncio.to_thread(started.wait, 2)
                async with client.get(origin + '/healthz') as health:
                    assert health.status == 200
                async with client.post(origin + PATH, json=BASE) as busy:
                    assert busy.status == 503
                    assert await busy.json() == {"error": "busy"}
                release.set()
                response = await first
                assert response.status == 200
                await response.read()
                response.release()
        finally:
            release.set()
            await runner.cleanup()
    asyncio.run(run())


def test_rate_window_and_global_limit():
    now = [0]
    verifier = Verifier(TOKEN, clock=lambda: now[0])
    for index in range(60):
        assert not verifier.limited(str(index))
    assert verifier.limited('new')
    now[0] = 61
    assert not verifier.limited('new')


def test_memory_guard_refuses_work():
    async def run():
        class Content:
            async def iter_chunked(self, _):
                yield json.dumps(BASE).encode()
        class Request:
            headers = {"Authorization": "Bearer " + TOKEN}
            content_type = "application/json"
            content = Content()
        def forbidden(_):
            raise AssertionError("password work must not run")
        verifier = Verifier(TOKEN, compute=forbidden, can_compute=lambda: False)
        response = await verifier.handle(Request())
        assert response.status == 503
        assert verifier.active is None
    asyncio.run(run())


def test_cancelled_request_keeps_cpu_slot_reserved():
    async def run():
        started, release = threading.Event(), threading.Event()
        def compute(_):
            started.set()
            release.wait(5)
            return {"matched": True}
        class Content:
            async def iter_chunked(self, _):
                yield json.dumps(BASE).encode()
        class Request:
            headers = {"Authorization": "Bearer " + TOKEN}
            content_type = "application/json"
            content = Content()
        verifier = Verifier(TOKEN, compute=compute)
        first = asyncio.create_task(verifier.handle(Request()))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            first.cancel()
            try:
                await first
            except asyncio.CancelledError:
                pass
            assert (await verifier.handle(Request())).status == 503
        finally:
            release.set()
            await verifier.active
    asyncio.run(run())

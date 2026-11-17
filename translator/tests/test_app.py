"""HTTP layer (app.py): API key, request validation, URL allow-list, the FIFO queue.

No models, no OCR server, no network: the ASGI app is called directly (the image has no httpx,
so no TestClient), with a fake pipeline and patched requests / DNS.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("API_KEY", "unit-test-key-0123456789")  # app.py reads its settings at import

from fastapi import HTTPException  # noqa: E402
from PIL import Image  # noqa: E402

import app as service  # noqa: E402
import pipeline  # noqa: E402

KEY = "unit-test-key-0123456789"
AUTH = {"x-api-key": KEY}
JSON = {"content-type": "application/json", **AUTH}


def png(size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


def multipart(fields: dict) -> tuple[dict, bytes]:
    """fields: name -> str (form field) or bytes (file)."""
    boundary = "testboundary"
    out = b""
    for name, value in fields.items():
        out += f"--{boundary}\r\n".encode()
        if isinstance(value, bytes):
            out += (f'Content-Disposition: form-data; name="{name}"; filename="x.png"\r\n'
                    "Content-Type: image/png\r\n\r\n").encode() + value + b"\r\n"
        else:
            out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    out += f"--{boundary}--\r\n".encode()
    return {"content-type": f"multipart/form-data; boundary={boundary}", **AUTH}, out


async def call(method: str, path: str, headers: dict | None = None, body: bytes = b"", query: str = ""):
    """One request through the ASGI app -> (status, headers, parsed JSON body or raw bytes)."""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
        "root_path": "", "client": ("test", 1), "server": ("test", 80),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        + [(b"content-length", str(len(body)).encode())],
    }
    sent = False
    gone = asyncio.Event()  # never set: the client stays connected
    messages = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()

    async def send(message):
        messages.append(message)

    await service.app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    headers_out = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw
    return start["status"], headers_out, parsed


def run(*a, **kw):
    return asyncio.run(call(*a, **kw))


class FakePipeline:
    """Pipeline.run stand-in: records its arguments; `gate` makes it block until released."""

    ocr_server = None

    def __init__(self, gate: threading.Event | None = None, error: Exception | None = None):
        self.calls: list[tuple] = []
        self.gate, self.error = gate, error
        self.started = threading.Event()

    def run(self, img, translate=True, context_tags=None):
        self.calls.append((img.size, translate, context_tags))
        self.started.set()
        if self.gate:
            assert self.gate.wait(10), "test never released the pipeline"
        if self.error:
            raise self.error
        return {"blocks": [], "n": len(self.calls)}


class AppCase(unittest.TestCase):
    """Fresh lock/state/settings per test (asyncio.Lock binds to the first loop that waits on it)."""

    settings = {}

    def setUp(self):
        self.pipeline = FakePipeline()
        s = dataclasses.replace(service.settings, **{"api_key": KEY, "allowed_url_hosts": ("client",),
                                                     "max_queue": 16, "max_image_mb": 40.0, **self.settings})
        for p in (mock.patch.object(service, "settings", s),
                  mock.patch.object(service, "work_lock", asyncio.Lock()),
                  mock.patch.object(service, "state", {"waiting": 0, "warming": False, "pipeline": self.pipeline})):
            p.start()
            self.addCleanup(p.stop)

    def post_image(self, **kw):
        headers, body = multipart({"image": png()})
        return run("POST", "/translate", headers, body, **kw)


class AuthTest(AppCase):
    def test_missing_key(self):
        status, _, body = run("POST", "/translate", {"content-type": "application/json"}, b"{}")
        self.assertEqual((status, body), (401, {"detail": "invalid or missing X-API-Key"}))
        self.assertEqual(self.pipeline.calls, [])

    def test_wrong_keys(self):
        for key in ("", "wrong", KEY[:-1], KEY + "x", KEY.upper(), "é" + KEY):
            headers, body = multipart({"image": png()})
            headers["x-api-key"] = key
            status, _, _ = run("POST", "/translate", headers, body)
            self.assertEqual(status, 401, key)
        self.assertEqual(self.pipeline.calls, [])

    def test_key_is_checked_before_the_body_is_looked_at(self):
        status, _, _ = run("POST", "/translate", {"content-type": "text/plain"}, b"x")
        self.assertEqual(status, 401)  # not 415

    def test_right_key(self):
        status, _, body = self.post_image()
        self.assertEqual((status, body), (200, {"blocks": [], "n": 1}))

    def test_key_only_in_the_header(self):
        headers, body = multipart({"image": png()})
        del headers["x-api-key"]
        status, _, _ = run("POST", "/translate", headers, body, query="x_api_key=" + KEY + "&X-API-Key=" + KEY)
        self.assertEqual(status, 401)

    def test_health_needs_no_key_and_tells_nothing_else(self):
        status, _, body = run("GET", "/health")
        self.assertEqual((status, body), (200, {"status": "ok", "busy": False, "queued": 0}))

    def test_no_docs_endpoints(self):
        for path in ("/docs", "/redoc"):
            self.assertEqual(run("GET", path)[0], 404, path)
        self.assertEqual(run("GET", "/translate", AUTH)[0], 405)

    def test_startup_refuses_a_missing_or_short_key(self):
        async def start(key):
            with mock.patch.object(service, "settings", dataclasses.replace(service.settings, api_key=key)), \
                    mock.patch.object(service, "Pipeline", side_effect=AssertionError("must not load")):
                async with service.lifespan(service.app):
                    pass

        for key in ("", "short", "x" * 15):
            with self.assertRaises(RuntimeError, msg=key):
                asyncio.run(start(key))


class HealthTest(AppCase):
    def test_states(self):
        service.state.pop("pipeline")
        self.assertEqual(run("GET", "/health")[2]["status"], "loading")
        service.state["pipeline"] = self.pipeline
        service.state["warming"] = True
        self.assertEqual(run("GET", "/health")[2]["status"], "warming")
        self.pipeline.ocr_server = mock.Mock(alive=lambda: False)
        self.assertEqual(run("GET", "/health")[2]["status"], "degraded")  # wins over warming
        self.pipeline.ocr_server = mock.Mock(alive=lambda: True)
        service.state["warming"] = False
        self.assertEqual(run("GET", "/health")[2]["status"], "ok")


class RequestTest(AppCase):
    def test_uploads_are_closed(self):
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            headers, body = multipart({"image": png(), "context_tags": "{}"})
            self.assertEqual(run("POST", "/translate", headers, body)[0], 200)
            headers, body = multipart({"other": png()})  # refused requests too
            self.assertEqual(run("POST", "/translate", headers, body)[0], 400)
            import gc
            gc.collect()
        self.assertEqual([str(w.message) for w in caught if issubclass(w.category, ResourceWarning)], [])

    def test_unsupported_content_type(self):
        status, _, _ = run("POST", "/translate", {"content-type": "text/plain", **AUTH}, b"x")
        self.assertEqual(status, 415)

    def test_bad_json_bodies(self):
        for body in (b"{", b"[]", b"{}", b'{"image_url": ""}', b'{"image_url": null}', b'"http://client/x.png"'):
            status, _, _ = run("POST", "/translate", JSON, body)
            self.assertEqual(status, 400, body)
        self.assertEqual(self.pipeline.calls, [])

    def test_multipart_without_an_image(self):
        for fields in ({}, {"image": "just text"}, {"other": png()}):
            headers, body = multipart(fields)
            self.assertEqual(run("POST", "/translate", headers, body)[0], 400, fields)

    def test_multipart_field_names(self):
        for name in ("image", "file"):
            headers, body = multipart({name: png((12, 7))})
            self.assertEqual(run("POST", "/translate", headers, body)[0], 200, name)
        self.assertEqual([c[0] for c in self.pipeline.calls], [(12, 7)] * 2)

    def test_not_an_image(self):
        headers, body = multipart({"image": b"GIF89a but not really"})
        status, _, detail = run("POST", "/translate", headers, body)
        self.assertEqual(status, 400)
        self.assertIn("not a readable image", detail["detail"])
        self.assertEqual(self.pipeline.calls, [])

    def test_file_too_large(self):
        with mock.patch.object(service, "settings", dataclasses.replace(service.settings, max_image_mb=0.00001)):
            self.assertEqual(self.post_image()[0], 413)
        self.assertEqual(self.pipeline.calls, [])

    def test_too_many_pixels(self):
        with mock.patch.object(pipeline, "MAX_PIXELS", 63):
            status, _, detail = self.post_image()
        self.assertEqual(status, 413)
        self.assertIn("8x8", detail["detail"])
        self.assertEqual(self.pipeline.calls, [])

    def test_translate_flag(self):
        self.post_image()
        self.post_image(query="translate=false")
        self.post_image(query="translate=true")
        self.assertEqual([c[1] for c in self.pipeline.calls], [True, False, True])
        self.assertEqual(self.post_image(query="translate=maybe")[0], 422)

    def test_context_tags_multipart(self):
        tags = {"character": ["cirno", " cirno ", 5, ""], "series": ["touhou"], "general": ["solo"]}
        headers, body = multipart({"image": png(), "context_tags": json.dumps(tags)})
        self.assertEqual(run("POST", "/translate", headers, body)[0], 200)
        self.assertEqual(self.pipeline.calls[0][2], {"character": ["cirno"], "series": ["touhou"]})

    def test_bad_context_tags(self):
        for raw in ("{", "[1]", '"x"', '{"character": "cirno"}'):
            headers, body = multipart({"image": png(), "context_tags": raw})
            self.assertEqual(run("POST", "/translate", headers, body)[0], 400, raw)
        with mock.patch.object(service, "fetch_url", side_effect=AssertionError("fetched")):
            body = json.dumps({"image_url": "http://client/x.png", "context_tags": [1]}).encode()
            self.assertEqual(run("POST", "/translate", JSON, body)[0], 400)  # before the fetch
        self.assertEqual(self.pipeline.calls, [])

    def test_json_image_url(self):
        with mock.patch.object(service, "fetch_url", return_value=png((5, 9))) as fetch:
            body = json.dumps({"image_url": "http://client/data/posts/1.png",
                               "context_tags": {"artist": ["zun"]}}).encode()
            status, _, _ = run("POST", "/translate", JSON, body)
        self.assertEqual(status, 200)
        fetch.assert_called_once_with("http://client/data/posts/1.png")
        self.assertEqual(self.pipeline.calls, [((5, 9), True, {"artist": ["zun"]})])

    def test_llm_error_is_a_502(self):
        self.pipeline.error = pipeline.LlmError("LLM HTTP 500: boom")
        status, _, detail = self.post_image()
        self.assertEqual((status, detail), (502, {"detail": "LLM HTTP 500: boom"}))


class FakeResponse:
    def __init__(self, status=200, chunks=(b"data",)):
        self.status_code, self.chunks = status, chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, _size):
        return iter(self.chunks)


def addrinfo(*ips):
    return lambda host, port: [(2, 1, 6, "", (ip, 0)) for ip in ips]


class FetchUrlTest(AppCase):
    """fetch_url: what the service is willing to download for {"image_url": ...}."""

    def fetch(self, url, response=None, hosts=("client",), dns=None):
        s = dataclasses.replace(service.settings, allowed_url_hosts=tuple(hosts))
        with mock.patch.object(service, "settings", s), \
                mock.patch.object(service.requests, "get", return_value=response or FakeResponse()) as get, \
                mock.patch.object(service.socket, "getaddrinfo",
                                  side_effect=dns or AssertionError("no DNS lookup expected")) as lookup:
            self.get, self.lookup = get, lookup
            return service.fetch_url(url)

    def refused(self, code, url, **kw):
        with self.assertRaises(HTTPException) as ctx:
            self.fetch(url, **kw)
        self.assertEqual(ctx.exception.status_code, code, url)
        self.get.assert_not_called()  # refused before any request
        return ctx.exception

    def test_allowed_host(self):
        self.assertEqual(self.fetch("http://client/data/posts/1.png"), b"data")
        self.get.assert_called_once()
        self.assertEqual(self.get.call_args.args, ("http://client/data/posts/1.png",))
        self.assertIs(self.get.call_args.kwargs["allow_redirects"], False)
        self.assertTrue(self.get.call_args.kwargs["timeout"])

    def test_host_match_is_case_insensitive_and_ignores_port_and_userinfo(self):
        for url in ("http://CLIENT/x.png", "http://client:8080/x.png", "https://user:pw@client/x.png"):
            self.assertEqual(self.fetch(url), b"data", url)

    def test_other_hosts_refused(self):
        for url in ("http://server/x.png", "http://evil.example/x.png", "http://client.evil.example/x.png",
                    "http://evil.example/?client", "http://evil.example#@client", "http://client@evil.example/x.png",
                    "http://127.0.0.1/x.png", "http://169.254.169.254/latest/meta-data/"):
            self.refused(403, url)

    def test_only_http_urls(self):
        for url in ("file:///etc/passwd", "ftp://client/x.png", "gopher://client/", "client/x.png", "//client/x.png",
                    "http:///x.png", "data:image/png;base64,AAAA", ""):
            self.refused(400, url)

    def test_no_allow_list_refuses_non_public_addresses(self):
        for ip in ("127.0.0.1", "10.0.0.5", "172.17.0.1", "192.168.1.1", "169.254.169.254", "::1", "fe80::1%eth0",
                   "fd00::1", "0.0.0.0", "240.0.0.1"):
            self.refused(403, "http://some.host/x.png", hosts=(), dns=addrinfo(ip))

    def test_no_allow_list_refuses_when_any_address_is_private(self):
        self.refused(403, "http://some.host/x.png", hosts=(), dns=addrinfo("93.184.216.34", "10.0.0.5"))

    def test_no_allow_list_allows_public_hosts(self):
        self.assertEqual(self.fetch("http://some.host/x.png", hosts=(), dns=addrinfo("93.184.216.34")), b"data")
        self.lookup.assert_called_once()

    def test_no_allow_list_unresolvable_host(self):
        def gaierror(host, port):
            raise service.socket.gaierror("no such host")

        self.refused(400, "http://nope.invalid/x.png", hosts=(), dns=gaierror)

    def test_no_allow_list_refuses_cgnat_and_multicast_addresses(self):
        for ip in ("100.100.1.1", "100.64.0.1", "224.0.0.1", "ff02::1", "192.0.2.1", "2001:db8::1"):
            self.refused(403, "http://some.host/x.png", hosts=(), dns=addrinfo(ip))

    def test_redirects_and_errors_are_not_followed(self):
        for status in (301, 302, 307, 403, 404, 500):
            with self.assertRaises(HTTPException) as ctx:
                self.fetch("http://client/x.png", FakeResponse(status))
            self.assertEqual(ctx.exception.status_code, 400, status)
            self.assertIn(str(status), ctx.exception.detail)

    def test_download_is_cut_off_at_the_size_limit(self):
        limit = service._max_bytes()
        chunk = b"x" * (64 * 1024)
        served = []

        def endless():
            while True:
                served.append(1)
                yield chunk

        with self.assertRaises(HTTPException) as ctx:
            self.fetch("http://client/x.png", FakeResponse(chunks=endless()))
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertLessEqual(len(served) * len(chunk), limit + 2 * len(chunk))  # stopped right after the limit

    def test_network_error(self):
        s = dataclasses.replace(service.settings, allowed_url_hosts=("client",))
        with mock.patch.object(service, "settings", s), \
                mock.patch.object(service.requests, "get", side_effect=service.requests.ConnectionError("refused")):
            with self.assertRaises(HTTPException) as ctx:
                service.fetch_url("http://client/x.png")
        self.assertEqual(ctx.exception.status_code, 400)


class QueueTest(AppCase):
    """One page in flight, up to MAX_QUEUE waiting, the rest get 503."""

    settings = {"max_queue": 1}

    async def until(self, cond, what):
        for _ in range(500):
            if cond():
                return
            await asyncio.sleep(0.01)
        self.fail("timed out waiting for " + what)

    def test_one_at_a_time_then_503(self):
        gate = threading.Event()
        self.pipeline.gate = gate
        headers, body = multipart({"image": png()})

        async def scenario():
            first = asyncio.create_task(call("POST", "/translate", headers, body))
            await self.until(self.pipeline.started.is_set, "the first request to reach the pipeline")
            busy = (await call("GET", "/health"))[2]
            second = asyncio.create_task(call("POST", "/translate", headers, body))
            await self.until(lambda: service.state["waiting"] == 1, "the second request to queue")
            queued = (await call("GET", "/health"))[2]
            third = await call("POST", "/translate", headers, body)  # queue full
            in_flight = len(self.pipeline.calls)
            gate.set()
            return busy, queued, third, in_flight, await first, await second

        busy, queued, third, in_flight, first, second = asyncio.run(scenario())
        self.assertEqual(busy, {"status": "ok", "busy": True, "queued": 0})
        self.assertEqual(queued, {"status": "ok", "busy": True, "queued": 1})
        self.assertEqual(third[0], 503)
        self.assertEqual(third[1]["retry-after"], "30")
        self.assertEqual(in_flight, 1)  # the waiting request had not started
        self.assertEqual((first[0], first[2]["n"]), (200, 1))
        self.assertEqual((second[0], second[2]["n"]), (200, 2))
        self.assertEqual(len(self.pipeline.calls), 2)  # the refused one never ran
        self.assertEqual(service.state["waiting"], 0)
        self.assertFalse(service.work_lock.locked())

    def test_waiting_requests_run_in_arrival_order(self):
        gate = threading.Event()
        self.pipeline.gate = gate
        sizes = [(8, 8), (9, 8), (10, 8), (11, 8)]

        async def scenario():
            with mock.patch.object(service, "settings", dataclasses.replace(service.settings, max_queue=10)):
                tasks = []
                for i, size in enumerate(sizes):
                    headers, body = multipart({"image": png(size)})
                    tasks.append(asyncio.create_task(call("POST", "/translate", headers, body)))
                    if i == 0:
                        await self.until(self.pipeline.started.is_set, "the first request")
                    else:
                        await self.until(lambda: service.state["waiting"] == i, f"request {i} to queue")
                gate.set()
                return [await t for t in tasks]

        results = asyncio.run(scenario())
        self.assertEqual([r[0] for r in results], [200] * 4)
        self.assertEqual([c[0] for c in self.pipeline.calls], sizes)

    def test_queue_of_zero_means_no_waiting(self):
        gate = threading.Event()
        self.pipeline.gate = gate
        headers, body = multipart({"image": png()})

        async def scenario():
            with mock.patch.object(service, "settings", dataclasses.replace(service.settings, max_queue=0)):
                first = asyncio.create_task(call("POST", "/translate", headers, body))
                await self.until(self.pipeline.started.is_set, "the first request")
                second = await call("POST", "/translate", headers, body)
                gate.set()
                return await first, second

        first, second = asyncio.run(scenario())
        self.assertEqual((first[0], second[0]), (200, 503))

    def test_idle_service_takes_a_request_even_with_a_queue_of_zero(self):
        with mock.patch.object(service, "settings", dataclasses.replace(service.settings, max_queue=0)):
            self.assertEqual(self.post_image()[0], 200)

    def test_lock_released_after_an_llm_error(self):
        self.pipeline.error = pipeline.LlmError("down")
        self.assertEqual(self.post_image()[0], 502)
        self.assertFalse(service.work_lock.locked())
        self.pipeline.error = None
        self.assertEqual(self.post_image()[0], 200)

    def test_lock_released_after_a_crash(self):
        self.pipeline.error = ZeroDivisionError("bug")
        with self.assertRaises(ZeroDivisionError):  # the ASGI server turns this into a 500
            self.post_image()
        self.assertFalse(service.work_lock.locked())
        self.assertEqual(service.state["waiting"], 0)
        self.pipeline.error = None
        self.assertEqual(self.post_image()[0], 200)

    def test_rejected_requests_do_not_take_a_queue_place(self):
        gate = threading.Event()
        self.pipeline.gate = gate
        headers, body = multipart({"image": png()})
        bad_headers, bad_body = multipart({"image": b"not an image"})

        async def scenario():
            first = asyncio.create_task(call("POST", "/translate", headers, body))
            await self.until(self.pipeline.started.is_set, "the first request")
            bad = [await call("POST", "/translate", bad_headers, bad_body),
                   await call("POST", "/translate", {"content-type": "application/json"}, b"{}")]
            waiting = service.state["waiting"]
            gate.set()
            return bad, waiting, await first

        bad, waiting, first = asyncio.run(scenario())
        self.assertEqual([b[0] for b in bad], [400, 401])
        self.assertEqual(waiting, 0)
        self.assertEqual(first[0], 200)

    def test_cancelled_while_waiting_gives_the_place_back(self):
        gate = threading.Event()
        self.pipeline.gate = gate
        headers, body = multipart({"image": png()})

        async def scenario():
            first = asyncio.create_task(call("POST", "/translate", headers, body))
            await self.until(self.pipeline.started.is_set, "the first request")
            second = asyncio.create_task(call("POST", "/translate", headers, body))
            await self.until(lambda: service.state["waiting"] == 1, "the second request to queue")
            second.cancel()
            await asyncio.gather(second, return_exceptions=True)
            waiting = service.state["waiting"]
            third = asyncio.create_task(call("POST", "/translate", headers, body))  # the place is free again
            await self.until(lambda: service.state["waiting"] == 1, "the third request to queue")
            gate.set()
            return waiting, await first, await third

        waiting, first, third = asyncio.run(scenario())
        self.assertEqual(waiting, 0)
        self.assertEqual((first[0], third[0]), (200, 200))
        self.assertEqual(len(self.pipeline.calls), 2)
        self.assertFalse(service.work_lock.locked())


class WarmupTest(AppCase):
    def test_warmup_holds_the_lock_and_clears_the_flag(self):
        seen = {}

        def warm():
            seen["locked"] = service.work_lock.locked()
            return {"detect_ms": 1}

        self.pipeline.warmup = warm
        service.state["warming"] = True
        asyncio.run(service.warmup())
        self.assertEqual(seen, {"locked": True})
        self.assertFalse(service.state["warming"])
        self.assertFalse(service.work_lock.locked())

    def test_failed_warmup_is_not_fatal(self):
        self.pipeline.warmup = mock.Mock(side_effect=RuntimeError("OCR server down"))
        service.state["warming"] = True
        with self.assertLogs("mtl", level="ERROR"):
            asyncio.run(service.warmup())
        self.assertFalse(service.state["warming"])
        self.assertFalse(service.work_lock.locked())


if __name__ == "__main__":
    unittest.main()

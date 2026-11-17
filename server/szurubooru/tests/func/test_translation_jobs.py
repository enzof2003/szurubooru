import json
from types import SimpleNamespace

import pytest

from szurubooru.func import translation_jobs as tj

POLY = [[0.1, 0.1], [0.2, 0.1], [0.2, 0.5], [0.1, 0.5]]


def _block(source, translation, bbox=None, color=None):
    block = {"polygon": POLY, "source": source, "translation": translation}
    if bbox is not None:
        block["bbox_px"] = bbox
    if color is not None:
        block["color"] = color
    return block


def test_note_text_plain():
    assert tj.note_text("藍さま", "Ran-sama") == "[MT] Ran-sama"
    assert tj.note_text("藍さま", "Ran-sama", "#E86800") == (
        "[MT]{c:#e86800} Ran-sama"
    )


def test_note_text_untranslated_placeholder():
    text = tj.note_text("藍さま", None, "#e86800")
    assert text == "[MT?]{c:#e86800} 藍さま"
    assert tj.is_mt_placeholder(text)
    assert not tj.is_mt_uncertain(text)


def test_note_text_uncertain():
    text = tj.note_text("「闘れぎ・・・？」", "A dildo...?", "#e86800", True)
    assert text == "[MT?]{c:#e86800} (uncertain) A dildo...?\n「闘れぎ・・・？」"
    assert tj.is_mt_note(text)
    assert tj.is_mt_uncertain(text)
    assert not tj.is_mt_placeholder(text)
    assert tj.is_mt_uncertain(tj.note_text("う", "Ugh", None, True))


def test_note_text_uncertain_without_translation_stays_placeholder():
    text = tj.note_text("のまOO", None, None, True)
    assert text == "[MT?] のまOO"
    assert tj.is_mt_placeholder(text)


def test_plan_notes_flags_only_garbled_blocks():
    blocks = [
        _block("「藍さま藍さまぁ～」", "Ran-sama~", [1286, 14, 1323, 367]),
        _block("うはNo.7つい", "Uha... No. 7...", [1196, 552, 1216, 806]),
        _block("う", "Ugh...", [212, 581, 230, 665], "#e6c600"),
    ]
    to_add, to_remove, skipped = tj.plan_notes([], blocks)
    texts = [note["text"] for note in to_add]
    assert (to_remove, skipped) == ([], 0)
    assert texts == [
        "[MT] Ran-sama~",
        "[MT?] (uncertain) Uha... No. 7...\nうはNo.7つい",
        "[MT?]{c:#e6c600} (uncertain) Ugh...\nう",
    ]


def test_plan_notes_flags_blocks_the_translator_marked_uncertain():
    marked = _block("「ど…ひろな子しば…いやつ」", "What a bold brat...",
                    [957, 8, 1004, 919], "#b75cd6")
    marked["uncertain"] = True
    unmarked = dict(marked, uncertain=False)
    to_add, _to_remove, _skipped = tj.plan_notes([], [marked, unmarked])
    assert [note["text"] for note in to_add] == [
        "[MT?]{c:#b75cd6} (uncertain) What a bold brat...\n"
        "「ど…ひろな子しば…いやつ」",
        "[MT]{c:#b75cd6} What a bold brat...",
    ]


def test_plan_notes_replaces_uncertain_notes():
    existing = [
        (POLY, "[MT?] (uncertain) old\nう"),
        (POLY, "human note"),
    ]
    _to_add, to_remove, _skipped = tj.plan_notes(existing, [])
    assert to_remove == [0]


def _tag(name):
    return SimpleNamespace(names=[SimpleNamespace(name=name)])


def _names(post):
    return sorted(tag.names[0].name for tag in post.tags)


def test_update_tags_adds_and_removes_flags(monkeypatch):
    monkeypatch.setattr(
        tj.tags,
        "get_or_create_tags_by_names",
        lambda names: ([], [_tag(name) for name in names]),
    )
    post = SimpleNamespace(tags=[_tag("reimu_hakurei")])

    created = tj._update_tags(post, incomplete=False, uncertain=True)
    assert [t.names[0].name for t in created] == [
        "machine_translated",
        "translation_uncertain",
    ]
    assert _names(post) == [
        "machine_translated", "reimu_hakurei", "translation_uncertain"
    ]

    tj._update_tags(post, incomplete=True, uncertain=False)
    assert _names(post) == [
        "machine_translated", "reimu_hakurei", "translation_incomplete"
    ]

    tj._update_tags(post, incomplete=False, uncertain=False)
    assert _names(post) == ["machine_translated", "reimu_hakurei"]


# --- queue: per-user cap, recovery of jobs left "running" -------------------


class _FakeSession:
    """Stands in for db.session: .query(...).filter(...).all() returns
    `rows`; add/flush/commit/rollback are counted."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []
        self.commits = 0

    def query(self, *_args):
        return self

    def filter(self, *_args):
        return self

    def all(self):
        return self.rows

    def first(self):
        return None

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def rollback(self):
        pass

    def commit(self):
        self.commits += 1


def _create_job_env(monkeypatch, active_jobs, limit=None):
    session = _FakeSession()
    monkeypatch.setattr(tj.db, "session", session)
    monkeypatch.setenv("TRANSLATE_API_KEY", "k" * 20)
    monkeypatch.setattr(tj, "_get_active_job", lambda post_id: None)
    monkeypatch.setattr(tj, "_count_active_jobs", lambda user_id: active_jobs)
    monkeypatch.setattr(
        tj.config,
        "config",
        {} if limit is None
        else {"translation": {"max_pending_jobs_per_user": limit}},
    )
    monkeypatch.setattr(
        tj.model, "TranslationJob",
        type("Job", (), {
            "STATUS_PENDING": "pending",
            "ACTIVE_STATUSES": ("pending", "running"),
        }),
    )
    post = SimpleNamespace(post_id=7, type=tj.model.Post.TYPE_IMAGE, tags=[])
    user = SimpleNamespace(user_id=3)
    return session, post, user


def test_job_cap_default_is_20(monkeypatch):
    session, post, user = _create_job_env(monkeypatch, active_jobs=19)
    job = tj.create_job(post, user)
    assert session.added == [job] and job.status == "pending"

    session, post, user = _create_job_env(monkeypatch, active_jobs=20)
    try:
        tj.create_job(post, user)
        assert False, "expected TooManyTranslationJobsError"
    except tj.TooManyTranslationJobsError as ex:
        assert "20" in str(ex)
    assert session.added == []


def test_job_cap_is_configurable_and_can_be_disabled(monkeypatch):
    session, post, user = _create_job_env(monkeypatch, 2, limit=2)
    try:
        tj.create_job(post, user)
        assert False, "expected TooManyTranslationJobsError"
    except tj.TooManyTranslationJobsError:
        pass
    session, post, user = _create_job_env(monkeypatch, 1, limit=2)
    tj.create_job(post, user)
    assert len(session.added) == 1
    # 0 = no limit
    session, post, user = _create_job_env(monkeypatch, 10 ** 6, limit=0)
    tj.create_job(post, user)
    assert len(session.added) == 1
    # nonsense in config.yaml falls back to the default
    session, post, user = _create_job_env(monkeypatch, 20, limit="lots")
    try:
        tj.create_job(post, user)
        assert False, "expected TooManyTranslationJobsError"
    except tj.TooManyTranslationJobsError:
        pass


def test_job_cap_is_a_validation_error():
    # shown to the user as a 400 with its message
    assert issubclass(tj.TooManyTranslationJobsError, tj.errors.ValidationError)


def _running(job_id, attempts):
    return SimpleNamespace(
        job_id=job_id, attempts=attempts, status="running", error=None,
        end_time=None,
    )


def test_recover_requeues_once_then_fails(monkeypatch):
    first, second = _running(1, attempts=1), _running(2, attempts=2)
    session = _FakeSession([first, second])
    monkeypatch.setattr(tj.db, "session", session)
    assert tj._recover_interrupted_jobs() == 2
    assert first.status == "pending" and first.error is None
    assert second.status == "failed" and second.end_time is not None
    assert "already retried" in second.error
    assert session.commits == 1


def test_job_whose_failure_could_not_be_saved_is_recovered(monkeypatch):
    """Database away when the job ends: _fail_job raises too, the row stays
    "running". The next idle pass of the worker loop must pick it up instead
    of leaving the post locked until a server restart."""
    stuck = _running(5, attempts=1)
    session = _FakeSession([stuck])
    monkeypatch.setattr(tj.db, "session", session)
    monkeypatch.setattr(tj.db.session, "remove", lambda: None, raising=False)
    events = []

    def process(job_id):
        raise RuntimeError("translator result arrived")

    def fail_job(job_id, message):
        events.append("fail_job")
        raise RuntimeError("database is away")

    class Stop(BaseException):
        pass

    def claim():
        events.append("claim:%s" % stuck.status)
        if len([e for e in events if e.startswith("claim")]) == 1:
            stuck.status = "running"
            return stuck.job_id
        raise Stop()

    monkeypatch.setattr(tj, "_process_job", process)
    monkeypatch.setattr(tj, "_fail_job", fail_job)
    monkeypatch.setattr(tj, "_claim_next_job", claim)
    monkeypatch.setattr(tj.time, "sleep", lambda _s: None)
    stuck.status = "pending"
    session.rows = []  # startup recovery: nothing to do
    recover = tj._recover_interrupted_jobs

    def recover_running():
        session.rows = [stuck] if stuck.status == "running" else []
        return recover()

    monkeypatch.setattr(tj, "_recover_interrupted_jobs", recover_running)
    try:
        tj._worker_loop()
    except Stop:
        pass
    # claimed, crashed, could not be failed ... and re-queued on the next pass
    assert events == ["claim:pending", "fail_job", "claim:pending"]
    assert stuck.status == "pending"


# --- call_translator against a local HTTP server ---------------------------


class _Translator:
    """A tiny HTTP server playing the translator: answers each request with
    the next (status, headers, body) of `replies` and records what it got."""

    def __init__(self, replies):
        import http.server
        import threading

        self.replies = list(replies)
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append(
                    SimpleNamespace(
                        path=self.path,
                        headers=self.headers,  # case-insensitive
                        body=json.loads(self.rfile.read(length)),
                    )
                )
                status, headers, body = outer.replies.pop(0)
                if callable(body):
                    body = body()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


OK_BODY = json.dumps(
    {"width": 1, "height": 2, "blocks": [{"source": "あ"}]}
).encode()


def _translator(monkeypatch, replies):
    server = _Translator(replies)
    monkeypatch.setenv("TRANSLATE_URL", server.url + "/")
    monkeypatch.setenv("TRANSLATE_API_KEY", "secret-key-0123456789")
    monkeypatch.delenv("TRANSLATE_LLM", raising=False)
    monkeypatch.delenv("TRANSLATE_TIMEOUT", raising=False)
    sleeps = []
    monkeypatch.setattr(tj.time, "sleep", sleeps.append)
    return server, sleeps


def test_call_translator_sends_key_url_and_context(monkeypatch):
    server, _sleeps = _translator(monkeypatch, [(200, {}, OK_BODY)])
    try:
        result = tj.call_translator(
            "http://client/data/posts/1_x.png", {"character": ["cirno"]}
        )
    finally:
        server.close()
    assert result["blocks"] == [{"source": "あ"}]
    (request,) = server.requests
    assert request.path == "/translate"
    assert request.headers["X-API-Key"] == "secret-key-0123456789"
    assert request.body == {
        "image_url": "http://client/data/posts/1_x.png",
        "context_tags": {"character": ["cirno"]},
    }


def test_call_translator_ocr_only_and_no_context(monkeypatch):
    server, _sleeps = _translator(monkeypatch, [(200, {}, OK_BODY)])
    monkeypatch.setenv("TRANSLATE_LLM", "0")
    try:
        tj.call_translator("http://client/data/posts/1_x.png", {})
    finally:
        server.close()
    (request,) = server.requests
    assert request.path == "/translate?translate=false"
    assert request.body == {"image_url": "http://client/data/posts/1_x.png"}


def test_call_translator_waits_out_a_full_queue(monkeypatch):
    server, sleeps = _translator(
        monkeypatch,
        [
            (503, {"Retry-After": "7"}, b'{"detail": "queue full"}'),
            (503, {"Retry-After": "soon"}, b""),  # unreadable: 30 s
            (503, {"Retry-After": "1"}, b""),  # at least 5 s
            (503, {"Retry-After": "9999"}, b""),  # at most 120 s
            (200, {}, OK_BODY),
        ],
    )
    try:
        assert tj.call_translator("http://x/1.png")["width"] == 1
    finally:
        server.close()
    assert sleeps == [7, 30, 5, 120]
    assert len(server.requests) == 5


def test_call_translator_gives_up_on_a_queue_that_stays_full(monkeypatch):
    monkeypatch.setattr(tj, "QUEUE_FULL_RETRIES", 3)
    server, sleeps = _translator(monkeypatch, [(503, {}, b"")] * 3)
    try:
        with pytest.raises(tj.TranslationError) as exc:
            tj.call_translator("http://x/1.png")
    finally:
        server.close()
    assert "queue stayed full" in str(exc.value)
    assert sleeps == [30, 30, 30]


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (
            502,
            b'{"detail": "LLM HTTP 500: upstream exploded"}',
            "Translator error (HTTP 502): LLM HTTP 500: upstream exploded",
        ),
        (401, b'{"detail": "invalid or missing X-API-Key"}',
         "Translator error (HTTP 401): invalid or missing X-API-Key"),
        (500, b"Internal Server Error",
         "Translator error (HTTP 500): Internal Server Error"),
        (500, b"x" * 1000, "Translator error (HTTP 500): " + "x" * 300),
        (500, b"[1, 2]", "Translator error (HTTP 500): [1, 2]"),
    ],
)
def test_call_translator_reports_http_errors(
    monkeypatch, status, body, expected
):
    server, sleeps = _translator(monkeypatch, [(status, {}, body)])
    try:
        with pytest.raises(tj.TranslationError) as exc:
            tj.call_translator("http://x/1.png")
    finally:
        server.close()
    assert str(exc.value) == expected
    assert sleeps == [] and len(server.requests) == 1  # no retry


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"<html>not json</html>", "Translator returned invalid JSON."),
        (b"\xff\xfe", "Translator returned invalid JSON."),
        (b"[]", "Translator returned an unexpected response."),
        (b'{"blocks": null}', "Translator returned an unexpected response."),
        (b'{"width": 1}', "Translator returned an unexpected response."),
    ],
)
def test_call_translator_rejects_bad_replies(monkeypatch, body, expected):
    server, _sleeps = _translator(monkeypatch, [(200, {}, body)])
    try:
        with pytest.raises(tj.TranslationError) as exc:
            tj.call_translator("http://x/1.png")
    finally:
        server.close()
    assert str(exc.value) == expected


def test_call_translator_times_out(monkeypatch):
    import threading

    release = threading.Event()

    def slow():
        release.wait(5)
        return OK_BODY

    server, _sleeps = _translator(monkeypatch, [(200, {}, slow)])
    monkeypatch.setenv("TRANSLATE_TIMEOUT", "0.3")
    try:
        with pytest.raises(tj.TranslationError) as exc:
            tj.call_translator("http://x/1.png")
    finally:
        release.set()
        server.close()
    assert "did not answer within" in str(exc.value)


def test_call_translator_unreachable(monkeypatch):
    server, _sleeps = _translator(monkeypatch, [])
    server.close()  # nothing listens on that port any more
    with pytest.raises(tj.TranslationError) as exc:
        tj.call_translator("http://x/1.png")
    assert str(exc.value).startswith("Translator is unreachable:")


def test_settings_from_the_environment(monkeypatch):
    for name in (
        "TRANSLATE_API_KEY", "TRANSLATE_URL", "TRANSLATE_IMAGE_BASE",
        "TRANSLATE_LLM", "TRANSLATE_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    assert not tj.is_configured()
    assert tj._translator_url() == "http://translate:8765"
    assert tj._image_base() == "http://client/data/"
    assert tj._use_llm() is True
    assert tj._timeout() == 900.0
    monkeypatch.setenv("TRANSLATE_API_KEY", "  ")
    assert not tj.is_configured()
    monkeypatch.setenv("TRANSLATE_API_KEY", " key ")
    assert tj.is_configured() and tj._api_key() == "key"
    monkeypatch.setenv("TRANSLATE_TIMEOUT", "soon")
    assert tj._timeout() == 900.0
    for value in ("0", "false", "No", " OFF "):
        monkeypatch.setenv("TRANSLATE_LLM", value)
        assert tj._use_llm() is False
    monkeypatch.setenv("TRANSLATE_LLM", "1")
    assert tj._use_llm() is True


def test_start_worker_does_nothing_when_not_configured(monkeypatch):
    monkeypatch.delenv("TRANSLATE_API_KEY", raising=False)
    monkeypatch.setattr(tj, "_worker_started", False)
    monkeypatch.setattr(
        tj.threading, "Thread",
        lambda **_kwargs: pytest.fail("worker must not start"),
    )
    tj.start_worker()
    assert tj._worker_started is False


def test_start_worker_starts_one_thread_only(monkeypatch):
    monkeypatch.setenv("TRANSLATE_API_KEY", "k" * 20)
    monkeypatch.setattr(tj, "_worker_started", False)
    started = []

    class Thread:
        def __init__(self, target, name, daemon):
            assert target is tj._worker_loop and daemon is True
            started.append(name)

        def start(self):
            pass

    monkeypatch.setattr(tj.threading, "Thread", Thread)
    tj.start_worker()
    tj.start_worker()
    assert started == ["translation-worker"]

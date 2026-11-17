from types import SimpleNamespace

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

"""
Translation job queue against a real PostgreSQL: create/claim/recover,
note merging with version conflicts, the per-user cap and the API routes.

These need PostgreSQL (FOR UPDATE SKIP LOCKED, the partial unique index, the
ARRAY columns of the schema), so they are opt-in: set SZURU_TEST_DATABASE to
the URL of an EMPTY throwaway database, e.g.

    docker network create szuru-test
    docker run -d --rm --name szuru-test-sql --network szuru-test \
        --tmpfs /var/lib/postgresql/data \
        -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test postgres:11-alpine
    docker run --rm -u 0 --network szuru-test \
        -e POSTGRES_USER=x -e POSTGRES_PASSWORD=x -e POSTGRES_HOST=x \
        -e SZURU_TEST_DATABASE=postgresql://test:test@szuru-test-sql/test \
        -v $PWD/server:/src:ro -w /src --entrypoint sh szuru-server -c \
        'apk add -q py3-pytest; python3 -m pytest -q --noconftest \
         -p no:cacheprovider szurubooru/tests/func/test_translation_queue_db.py'

The tables are created in that database and dropped afterwards. A database
that already has tables is refused, so the live one can't be hit by mistake.
Without the variable every test here is skipped.
"""
import os
import re
from datetime import datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
import sqlalchemy.orm

from szurubooru import config, db, errors, model
from szurubooru.api import translation_api
from szurubooru.func import posts
from szurubooru.func import translation_jobs as tj

DB_URL = os.getenv("SZURU_TEST_DATABASE")
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="SZURU_TEST_DATABASE is not set"
)

Job = model.TranslationJob
POLY = [[0.1, 0.1], [0.2, 0.1], [0.2, 0.5], [0.1, 0.5]]


# --- fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def engine():
    engine = sa.create_engine(DB_URL)
    existing = sa.inspect(engine).get_table_names()
    if existing:
        pytest.exit(
            "SZURU_TEST_DATABASE is not empty (%d tables): refusing to "
            "touch it" % len(existing)
        )
    model.Base.metadata.create_all(engine)
    # Same index as migration d97f7e581cf8 (it isn't declared on the model).
    engine.execute(
        "CREATE UNIQUE INDEX ix_translation_job_one_active_per_post "
        "ON translation_job (post_id) "
        "WHERE status IN ('pending', 'running')"
    )
    try:
        yield engine
    finally:
        model.Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture(autouse=True)
def session(engine, monkeypatch):
    session = sa.orm.scoped_session(
        sa.orm.sessionmaker(bind=engine, autoflush=False)
    )
    monkeypatch.setattr(db, "session", session)
    monkeypatch.setenv("TRANSLATE_API_KEY", "k" * 20)
    monkeypatch.delenv("TRANSLATE_LLM", raising=False)
    monkeypatch.setitem(config.config, "webhooks", [])
    monkeypatch.setitem(
        config.config,
        "privileges",
        dict(config.config["privileges"], **{"posts:edit:notes": "regular"}),
    )
    monkeypatch.setitem(config.config, "translation", {})
    monkeypatch.setattr(tj.time, "sleep", lambda _seconds: None)
    tj._wake.clear()
    try:
        yield session
    finally:
        session.remove()
        engine.execute(
            "TRUNCATE %s RESTART IDENTITY CASCADE"
            % ", ".join(
                '"%s"' % table.name
                for table in model.Base.metadata.sorted_tables
            )
        )


def _user(name="alice", rank=model.User.RANK_REGULAR):
    user = model.User()
    user.name = name
    user.password_salt = "dummy"
    user.password_hash = "dummy"
    user.email = "dummy"
    user.rank = rank
    user.creation_time = datetime(1997, 1, 1)
    user.avatar_style = model.User.AVATAR_GRAVATAR
    db.session.add(user)
    db.session.flush()
    return user


def _category(name, default=False):
    category = model.TagCategory()
    category.name = name
    category.color = "default"
    category.order = 1
    category.default = default
    db.session.add(category)
    return category


def _tag(name, category):
    tag = model.Tag()
    tag.names = [model.TagName(name, 0)]
    tag.category = category
    tag.creation_time = datetime(1996, 1, 1)
    db.session.add(tag)
    return tag


def _post(type=model.Post.TYPE_IMAGE, tags=(), notes=()):
    post = model.Post()
    post.safety = model.Post.SAFETY_SAFE
    post.type = type
    post.checksum = "..."
    post.flags = []
    post.mime_type = "image/png"
    post.creation_time = datetime(1996, 1, 1)
    post.tags = list(tags)
    for text in notes:
        post.notes.append(model.PostNote(polygon=POLY, text=text))
    db.session.add(post)
    db.session.flush()
    return post


def _job(post, user=None, status=Job.STATUS_PENDING, attempts=0):
    job = Job()
    job.post_id = post.post_id
    job.user = user
    job.status = status
    job.attempts = attempts
    job.creation_time = datetime(2026, 1, 1)
    db.session.add(job)
    db.session.flush()
    return job


def _reload(job_id):
    db.session.expire_all()
    return db.session.query(Job).get(job_id)


def _note_texts(post_id):
    db.session.expire_all()
    post = db.session.query(model.Post).get(post_id)
    return sorted(note.text for note in post.notes)


def _tag_names(post_id):
    db.session.expire_all()
    post = db.session.query(model.Post).get(post_id)
    return sorted(tag.names[0].name for tag in post.tags)


def _block(source, translation, **extra):
    return dict(polygon=POLY, source=source, translation=translation, **extra)


# --- create_job ------------------------------------------------------------


def test_create_job_queues_a_pending_job():
    user, post = _user(), _post()
    job = tj.create_job(post, user)
    db.session.commit()
    job = _reload(job.job_id)
    assert job.status == Job.STATUS_PENDING
    assert (job.post_id, job.user_id) == (post.post_id, user.user_id)
    assert job.attempts == 0 and job.creation_time is not None
    assert job.start_time is None and job.end_time is None


def test_create_job_refused_when_not_configured(monkeypatch):
    monkeypatch.delenv("TRANSLATE_API_KEY")
    with pytest.raises(tj.TranslationNotConfiguredError):
        tj.create_job(_post(), _user())


@pytest.mark.parametrize(
    "post_type", [model.Post.TYPE_VIDEO, model.Post.TYPE_FLASH]
)
def test_create_job_refused_for_videos_and_flash(post_type):
    with pytest.raises(tj.InvalidTranslationPostTypeError):
        tj.create_job(_post(type=post_type), _user())


def test_create_job_accepts_gifs():
    assert tj.create_job(_post(type=model.Post.TYPE_ANIMATION), _user())


def test_create_job_refused_for_translated_post_until_tag_removed():
    category = _category("default", default=True)
    tag = _tag("Machine_Translated", category)  # tag names: case-insensitive
    user, post = _user(), _post(tags=[tag])
    with pytest.raises(tj.PostAlreadyTranslatedError) as exc:
        tj.create_job(post, user)
    assert "machine_translated" in str(exc.value)
    post.tags = []
    assert tj.create_job(post, user)


@pytest.mark.parametrize("status", Job.ACTIVE_STATUSES)
def test_create_job_refused_while_one_is_active(status):
    user, post = _user(), _post()
    _job(post, user, status=status)
    db.session.commit()
    with pytest.raises(tj.TranslationJobAlreadyActiveError):
        tj.create_job(post, user)


@pytest.mark.parametrize("status", [Job.STATUS_DONE, Job.STATUS_FAILED])
def test_create_job_allowed_after_a_finished_one(status):
    user, post = _user(), _post()
    _job(post, user, status=status)
    db.session.commit()
    assert tj.create_job(post, user).status == Job.STATUS_PENDING


def test_unique_index_stops_two_simultaneous_requests(monkeypatch):
    """Both requests pass the _get_active_job check before either commits:
    the partial unique index is what refuses the second one."""
    user, post = _user(), _post()
    monkeypatch.setattr(tj, "_get_active_job", lambda post_id: None)
    tj.create_job(post, user)
    db.session.commit()
    post_id, user_id = post.post_id, user.user_id
    with pytest.raises(tj.TranslationJobAlreadyActiveError):
        tj.create_job(post, user)
    # the session was rolled back and is usable again
    assert db.session.query(Job).count() == 1
    # jobs for other posts are unaffected by the index
    other = _post()
    user = db.session.query(model.User).get(user_id)
    assert tj.create_job(other, user)
    db.session.commit()
    assert db.session.query(Job).filter(Job.post_id == post_id).count() == 1


# --- per-user cap ----------------------------------------------------------


def test_count_active_jobs_counts_only_that_users_pending_and_running():
    alice, bob = _user("alice"), _user("bob")
    for status in (
        Job.STATUS_PENDING,
        Job.STATUS_RUNNING,
        Job.STATUS_DONE,
        Job.STATUS_FAILED,
    ):
        _job(_post(), alice, status=status)
    _job(_post(), bob)
    _job(_post(), None)
    db.session.commit()
    assert tj._count_active_jobs(alice.user_id) == 2
    assert tj._count_active_jobs(bob.user_id) == 1
    assert tj._count_active_jobs(10 ** 6) == 0


def test_job_cap_per_user(monkeypatch):
    monkeypatch.setitem(
        config.config, "translation", {"max_pending_jobs_per_user": 2}
    )
    alice, bob = _user("alice"), _user("bob")
    first = tj.create_job(_post(), alice)
    tj.create_job(_post(), alice)
    db.session.commit()
    with pytest.raises(tj.TooManyTranslationJobsError) as exc:
        tj.create_job(_post(), alice)
    assert "2" in str(exc.value)
    # someone else's queue is their own
    assert tj.create_job(_post(), bob)
    db.session.commit()
    # a finished job frees a place
    _reload(first.job_id).status = Job.STATUS_DONE
    db.session.commit()
    alice = db.session.query(model.User).filter_by(name="alice").one()
    assert tj.create_job(_post(), alice)


def test_job_cap_default_and_off(monkeypatch):
    alice = _user()
    for _ in range(tj.DEFAULT_MAX_PENDING_JOBS_PER_USER):
        _job(_post(), alice)
    db.session.commit()
    with pytest.raises(tj.TooManyTranslationJobsError):
        tj.create_job(_post(), alice)
    monkeypatch.setitem(
        config.config, "translation", {"max_pending_jobs_per_user": 0}
    )
    assert tj.create_job(_post(), alice)


# --- queue position --------------------------------------------------------


def test_queue_position_counts_active_jobs_ahead():
    user = _user()
    done = _job(_post(), user, status=Job.STATUS_DONE)
    running = _job(_post(), user, status=Job.STATUS_RUNNING)
    second = _job(_post(), user)
    third = _job(_post(), user)
    db.session.commit()
    assert tj._queue_position(done) is None
    assert tj._queue_position(running) is None
    assert tj._queue_position(second) == 1
    assert tj._queue_position(third) == 2


# --- claim -----------------------------------------------------------------


def test_claim_takes_the_oldest_pending_job():
    user = _user()
    _job(_post(), user, status=Job.STATUS_DONE)
    _job(_post(), user, status=Job.STATUS_FAILED)
    first = _job(_post(), user)
    second = _job(_post(), user)
    first.error = "left over from an earlier attempt"
    db.session.commit()
    first_id, second_id = first.job_id, second.job_id

    assert tj._claim_next_job() == first_id
    job = _reload(first_id)
    assert job.status == Job.STATUS_RUNNING
    assert job.attempts == 1
    assert job.start_time is not None
    assert job.error is None
    assert _reload(second_id).status == Job.STATUS_PENDING

    assert tj._claim_next_job() == second_id
    assert tj._claim_next_job() is None


def test_claim_returns_none_on_an_empty_queue():
    assert tj._claim_next_job() is None


def test_claim_skips_a_row_locked_by_another_transaction(engine):
    user = _user()
    first, second = _job(_post(), user), _job(_post(), user)
    db.session.commit()
    first_id, second_id = first.job_id, second.job_id
    other = engine.connect()
    transaction = other.begin()
    try:
        other.execute(
            "SELECT id FROM translation_job WHERE id = %s FOR UPDATE",
            (first_id,),
        )
        assert tj._claim_next_job() == second_id  # no waiting on the lock
    finally:
        transaction.rollback()
        other.close()
    assert tj._claim_next_job() == first_id


# --- recovery --------------------------------------------------------------


def test_recovery_requeues_once_then_fails():
    user = _user()
    once = _job(_post(), user, status=Job.STATUS_RUNNING, attempts=1)
    twice = _job(
        _post(), user, status=Job.STATUS_RUNNING, attempts=tj.MAX_ATTEMPTS
    )
    pending = _job(_post(), user)
    done = _job(_post(), user, status=Job.STATUS_DONE, attempts=1)
    db.session.commit()
    ids = [job.job_id for job in (once, twice, pending, done)]

    assert tj._recover_interrupted_jobs() == 2
    once, twice, pending, done = [_reload(job_id) for job_id in ids]
    assert once.status == Job.STATUS_PENDING and once.attempts == 1
    assert twice.status == Job.STATUS_FAILED
    assert "already retried" in twice.error and twice.end_time is not None
    assert pending.status == Job.STATUS_PENDING
    assert done.status == Job.STATUS_DONE

    # the re-queued job runs again; interrupted a second time it fails
    assert tj._claim_next_job() == once.job_id
    assert tj._recover_interrupted_jobs() == 1
    assert _reload(once.job_id).status == Job.STATUS_FAILED
    assert tj._recover_interrupted_jobs() == 0


def test_recovered_post_accepts_a_new_job():
    """A row stuck "running" keeps the unique index busy; once recovery has
    failed it, the post can be queued again."""
    user, post = _user(), _post()
    _job(post, user, status=Job.STATUS_RUNNING, attempts=tj.MAX_ATTEMPTS)
    db.session.commit()
    with pytest.raises(tj.TranslationJobAlreadyActiveError):
        tj.create_job(post, user)
    tj._recover_interrupted_jobs()
    post = db.session.query(model.Post).one()
    user = db.session.query(model.User).one()
    assert tj.create_job(post, user)


# --- _fail_job / _store_result ---------------------------------------------


def test_fail_job_records_the_message_and_discards_pending_changes():
    user, post = _user(), _post()
    job = _job(post, user, status=Job.STATUS_RUNNING, attempts=1)
    db.session.commit()
    job_id, post_id = job.job_id, post.post_id
    post.notes.append(model.PostNote(polygon=POLY, text="half-written"))
    tj._fail_job(job_id, "x" * 5000)
    job = _reload(job_id)
    assert job.status == Job.STATUS_FAILED
    assert job.error == "x" * 2000
    assert job.end_time is not None
    assert _note_texts(post_id) == []


def test_fail_job_and_store_result_tolerate_a_deleted_job():
    tj._fail_job(12345, "gone")
    tj._store_result(12345, {"blocks": []})


def test_store_result_keeps_blocks_and_size_only():
    job = _job(_post(), _user(), status=Job.STATUS_RUNNING)
    db.session.commit()
    blocks = [_block("あ", "a")]
    tj._store_result(
        job.job_id,
        {"width": 10, "height": 20, "blocks": blocks, "meta": {"x": 1}},
    )
    assert _reload(job.job_id).result == {
        "width": 10, "height": 20, "blocks": blocks
    }


# --- merge -----------------------------------------------------------------


def _merge_setup(notes=()):
    _category("default", default=True)
    user = _user()
    post = _post(notes=notes)
    job = _job(post, user, status=Job.STATUS_RUNNING, attempts=1)
    db.session.commit()
    return job.job_id, post.post_id, user.user_id


def test_merge_replaces_mt_notes_and_keeps_human_ones():
    job_id, post_id, user_id = _merge_setup(
        notes=["a human note", "[MT] old line", "[MT?] 古い"]
    )
    blocks = [
        _block("こんにちは", "Hello"),
        _block("やあ", "Hi", color="#AABBCC"),
        {"polygon": [[0, 0]], "source": "x", "translation": "y"},
        "not a block",
    ]
    tj._merge_result(job_id, post_id, blocks)

    assert _note_texts(post_id) == [
        "[MT] Hello", "[MT]{c:#aabbcc} Hi", "a human note"
    ]
    assert _tag_names(post_id) == ["machine_translated"]
    job = _reload(job_id)
    assert job.status == Job.STATUS_DONE and job.end_time is not None
    assert (job.notes_added, job.notes_replaced, job.notes_skipped) == (
        2, 2, 2
    )
    post = db.session.query(model.Post).get(post_id)
    assert post.version == 2
    assert post.last_edit_time is not None
    # history: the tag's creation and the post's change, as the requester
    snapshots = db.session.query(model.Snapshot).all()
    assert sorted(
        (s.resource_type, s.operation, s.user_id) for s in snapshots
    ) == [
        ("post", model.Snapshot.OPERATION_MODIFIED, user_id),
        ("tag", model.Snapshot.OPERATION_CREATED, user_id),
    ]


def test_merge_flags_incomplete_and_uncertain_runs_and_clears_them_later():
    job_id, post_id, _user_id = _merge_setup()
    tj._merge_result(
        job_id,
        post_id,
        [
            _block("こんにちは", None),  # the LLM gave nothing
            _block("やあ", "Hi", uncertain=True),
        ],
    )
    assert _note_texts(post_id) == [
        "[MT?] (uncertain) Hi\nやあ", "[MT?] こんにちは"
    ]
    assert _tag_names(post_id) == [
        "machine_translated", "translation_incomplete",
        "translation_uncertain",
    ]

    # a later clean run (the user removed machine_translated to redo it)
    post = db.session.query(model.Post).get(post_id)
    post.tags = [
        tag for tag in post.tags
        if tag.names[0].name != "machine_translated"
    ]
    again = _job(post, None, status=Job.STATUS_RUNNING, attempts=1)
    db.session.commit()
    tj._merge_result(again.job_id, post_id, [_block("こんにちは", "Hello")])
    assert _note_texts(post_id) == ["[MT] Hello"]
    assert _tag_names(post_id) == ["machine_translated"]
    assert _reload(again.job_id).notes_replaced == 2


def test_merge_with_no_blocks_still_finishes_and_tags():
    job_id, post_id, _user_id = _merge_setup(notes=["[MT] old"])
    tj._merge_result(job_id, post_id, [])
    assert _note_texts(post_id) == []
    assert _tag_names(post_id) == ["machine_translated"]
    job = _reload(job_id)
    assert (job.status, job.notes_added, job.notes_replaced) == (
        Job.STATUS_DONE, 0, 1
    )


def test_merge_retries_when_the_post_was_edited_meanwhile(engine, monkeypatch):
    """Someone saves the post between the worker's read and its write: the
    version check fails (StaleDataError), the merge starts over on the new
    state and nothing is written twice."""
    job_id, post_id, _user_id = _merge_setup(notes=["[MT] old"])
    real_plan = tj.plan_notes
    calls = []

    def plan_and_get_overtaken(existing, blocks):
        calls.append(len(existing))
        if len(calls) == 1:
            engine.execute(
                "UPDATE post SET version = version + 1 WHERE id = %s",
                (post_id,),
            )
        return real_plan(existing, blocks)

    monkeypatch.setattr(tj, "plan_notes", plan_and_get_overtaken)
    tj._merge_result(job_id, post_id, [_block("こんにちは", "Hello")])

    assert len(calls) == 2
    assert _note_texts(post_id) == ["[MT] Hello"]
    assert db.session.query(model.Post).get(post_id).version == 3
    job = _reload(job_id)
    assert (job.status, job.notes_added, job.notes_replaced) == (
        Job.STATUS_DONE, 1, 1
    )
    assert db.session.query(model.Snapshot).filter_by(
        resource_type="post"
    ).count() == 1


def test_merge_gives_up_when_the_post_keeps_changing(engine, monkeypatch):
    job_id, post_id, _user_id = _merge_setup(notes=["[MT] old"])
    real_plan = tj.plan_notes
    calls = []

    def always_overtaken(existing, blocks):
        calls.append(1)
        engine.execute(
            "UPDATE post SET version = version + 1 WHERE id = %s", (post_id,)
        )
        return real_plan(existing, blocks)

    monkeypatch.setattr(tj, "plan_notes", always_overtaken)
    with pytest.raises(tj.TranslationError) as exc:
        tj._merge_result(job_id, post_id, [_block("こんにちは", "Hello")])
    assert "kept changing" in str(exc.value)
    assert len(calls) == tj.MERGE_RETRIES
    # nothing was written, and the job is still the worker's to fail
    assert _note_texts(post_id) == ["[MT] old"]
    assert _tag_names(post_id) == []
    assert _reload(job_id).status == Job.STATUS_RUNNING


def test_merge_when_the_post_was_deleted():
    job_id, post_id, _user_id = _merge_setup()
    # the job row survives here only because the lookup is patched; in the
    # database the job is deleted with its post (ON DELETE CASCADE)
    with pytest.raises(tj.TranslationError) as exc:
        tj._merge_result(job_id, post_id + 1000, [])
    assert "deleted" in str(exc.value)


def test_deleting_the_post_deletes_its_jobs():
    job_id, post_id, _user_id = _merge_setup()
    db.session.delete(db.session.query(model.Post).get(post_id))
    db.session.commit()
    assert _reload(job_id) is None
    tj._merge_result(job_id, post_id, [_block("あ", "a")])  # silently nothing
    assert db.session.query(model.PostNote).count() == 0


def test_deleting_the_user_keeps_the_job():
    job_id, post_id, user_id = _merge_setup()
    db.session.delete(db.session.query(model.User).get(user_id))
    db.session.commit()
    assert _reload(job_id).user_id is None
    tj._merge_result(job_id, post_id, [_block("こんにちは", "Hello")])
    assert _note_texts(post_id) == ["[MT] Hello"]
    assert _reload(job_id).status == Job.STATUS_DONE


# --- _process_job / _run_one -----------------------------------------------


def test_run_one_translates_and_merges(monkeypatch):
    default = _category("default", default=True)
    character = _category("character")
    tags = [
        _tag("reimu_hakurei", character),
        _tag("original_character", character),  # says nothing: not sent
        _tag("solo", default),
    ]
    user = _user()
    post = _post(tags=tags)
    job = _job(post, user)
    db.session.commit()
    post_id = post.post_id
    seen = {}

    def translator(image_url, context):
        seen["url"], seen["context"] = image_url, context
        return {
            "width": 100, "height": 200,
            "blocks": [_block("こんにちは", "Hello")],
        }

    monkeypatch.setattr(tj, "call_translator", translator)
    monkeypatch.setenv("TRANSLATE_IMAGE_BASE", "http://client/data")
    job_id = tj._claim_next_job()
    assert job_id == job.job_id
    tj._run_one(job_id)

    assert seen["url"] == "http://client/data/" + (
        posts.get_post_content_path(db.session.query(model.Post).get(post_id))
    )
    assert seen["context"] == {"character": ["reimu_hakurei"]}
    job = _reload(job_id)
    assert job.status == Job.STATUS_DONE and job.error is None
    assert job.result["blocks"][0]["translation"] == "Hello"
    assert _note_texts(post_id) == ["[MT] Hello"]
    assert "machine_translated" in _tag_names(post_id)


def test_run_one_records_a_translator_failure(monkeypatch):
    user, post = _user(), _post(notes=["[MT] old"])
    _job(post, user)
    db.session.commit()
    post_id = post.post_id

    def translator(image_url, context):
        raise tj.TranslationError("Translator error (HTTP 502): LLM down")

    monkeypatch.setattr(tj, "call_translator", translator)
    job_id = tj._claim_next_job()
    tj._run_one(job_id)
    job = _reload(job_id)
    assert job.status == Job.STATUS_FAILED
    assert job.error == "Translator error (HTTP 502): LLM down"
    assert job.end_time is not None
    assert _note_texts(post_id) == ["[MT] old"]  # the old notes stay
    assert _tag_names(post_id) == []
    # failed is final: nothing left to claim or recover, and a retry is
    # a new job
    assert tj._claim_next_job() is None
    assert tj._recover_interrupted_jobs() == 0


def test_run_one_records_an_unexpected_crash(monkeypatch):
    _job(_post(), _user())
    db.session.commit()

    def translator(image_url, context):
        raise KeyError("blocks")

    monkeypatch.setattr(tj, "call_translator", translator)
    job_id = tj._claim_next_job()
    tj._run_one(job_id)
    job = _reload(job_id)
    assert job.status == Job.STATUS_FAILED
    assert job.error == "Internal error: KeyError: 'blocks'"


def test_run_one_fails_a_job_whose_post_became_a_video(monkeypatch):
    user, post = _user(), _post()
    _job(post, user)
    post.type = model.Post.TYPE_VIDEO  # content replaced after queueing
    db.session.commit()
    monkeypatch.setattr(
        tj, "call_translator", lambda *a: pytest.fail("must not be called")
    )
    job_id = tj._claim_next_job()
    tj._run_one(job_id)
    job = _reload(job_id)
    assert job.status == Job.STATUS_FAILED
    assert "Only image and GIF" in job.error


# --- API routes ------------------------------------------------------------


def _ctx(user):
    return SimpleNamespace(user=user, session=db.session)


def test_api_post_queues_a_job_and_wakes_the_worker():
    user, post = _user(), _post()
    db.session.commit()
    result = translation_api.create_translation_job(
        _ctx(user), {"post_id": str(post.post_id)}
    )
    assert result["status"] == Job.STATUS_PENDING
    assert result["postId"] == post.post_id
    assert result["queuePosition"] == 0
    assert result["user"]["name"] == "alice"
    assert result["error"] is None and result["notesAdded"] is None
    assert tj._wake.is_set()
    db.session.rollback()  # the route committed: the job is really there
    assert db.session.query(Job).count() == 1


def test_api_post_reports_the_queue_position():
    user = _user()
    _job(_post(), user, status=Job.STATUS_RUNNING)
    _job(_post(), user)
    post = _post()
    db.session.commit()
    result = translation_api.create_translation_job(
        _ctx(user), {"post_id": post.post_id}
    )
    assert result["queuePosition"] == 2


def test_api_get_returns_the_latest_job():
    user, post = _user(), _post()
    db.session.commit()
    params = {"post_id": str(post.post_id)}
    assert translation_api.get_translation_job(_ctx(user), params) == {
        "status": None
    }
    old = _job(post, user, status=Job.STATUS_FAILED)
    old.error = "first try"
    new = _job(post, user, status=Job.STATUS_DONE)
    new.notes_added, new.notes_replaced, new.notes_skipped = 3, 1, 0
    db.session.commit()
    result = translation_api.get_translation_job(_ctx(user), params)
    assert result["id"] == new.job_id
    assert result["status"] == Job.STATUS_DONE
    assert (
        result["notesAdded"], result["notesReplaced"], result["notesSkipped"]
    ) == (3, 1, 0)
    assert result["queuePosition"] is None
    assert "result" not in result  # raw blocks are not exposed


@pytest.mark.parametrize(
    "route",
    [
        translation_api.create_translation_job,
        translation_api.get_translation_job,
    ],
)
def test_api_needs_the_note_editing_privilege(route, monkeypatch):
    post = _post()
    regular = _user("regular")
    anonymous = model.User()
    anonymous.name = None
    anonymous.rank = model.User.RANK_ANONYMOUS
    db.session.commit()
    params = {"post_id": str(post.post_id)}
    with pytest.raises(errors.AuthError):
        route(_ctx(anonymous), params)
    monkeypatch.setitem(
        config.config,
        "privileges",
        dict(config.config["privileges"], **{"posts:edit:notes": "power"}),
    )
    with pytest.raises(errors.AuthError):
        route(_ctx(regular), params)
    assert db.session.query(Job).count() == 0
    assert not tj._wake.is_set()


@pytest.mark.parametrize(
    "route",
    [
        translation_api.create_translation_job,
        translation_api.get_translation_job,
    ],
)
def test_api_bad_and_unknown_post_ids(route):
    user = _user()
    db.session.commit()
    with pytest.raises(posts.InvalidPostIdError):
        route(_ctx(user), {"post_id": "abc"})
    with pytest.raises(posts.PostNotFoundError):
        route(_ctx(user), {"post_id": "99999"})


def test_api_post_errors_do_not_wake_the_worker():
    user, post = _user(), _post()
    _job(post, user)
    db.session.commit()
    with pytest.raises(tj.TranslationJobAlreadyActiveError):
        translation_api.create_translation_job(
            _ctx(user), {"post_id": post.post_id}
        )
    assert not tj._wake.is_set()


def test_api_post_over_the_cap(monkeypatch):
    monkeypatch.setitem(
        config.config, "translation", {"max_pending_jobs_per_user": 1}
    )
    user = _user()
    _job(_post(), user)
    post = _post()
    db.session.commit()
    with pytest.raises(tj.TooManyTranslationJobsError):
        translation_api.create_translation_job(
            _ctx(user), {"post_id": post.post_id}
        )
    assert db.session.query(Job).count() == 1


def test_routes_are_registered():
    from szurubooru import rest

    for method, handler in (
        ("POST", translation_api.create_translation_job),
        ("GET", translation_api.get_translation_job),
    ):
        patterns = [
            url
            for url, handlers in rest.routes.routes.items()
            if handlers.get(method) is handler
        ]
        assert len(patterns) == 1
        match = re.fullmatch(patterns[0], "/post/12/translate")
        assert match and match.group("post_id") == "12"
        assert re.fullmatch(patterns[0], "/post/12/translate/")
        assert not re.fullmatch(patterns[0], "/post/12/translate/x")

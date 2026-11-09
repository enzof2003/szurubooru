"""Background machine translation of post notes.

The browser never talks to the translator. POST /api/post/<id>/translate
queues a TranslationJob row; one worker thread in this process takes jobs
FIFO, sends the post image to the translator service and merges the
resulting blocks into the post's notes (as the user who asked).

Configuration (environment of the server container):
    TRANSLATE_API_KEY     translator X-API-Key; feature disabled when unset
    TRANSLATE_URL         default http://translate:8765
    TRANSLATE_IMAGE_BASE  where the translator fetches post files,
                          default http://client/data/
    TRANSLATE_LLM         1 (default) = ask for translations; 0 = OCR only
                          (?translate=false), every note becomes "[MT?]"
    TRANSLATE_TIMEOUT     seconds to wait for one translator response, 900

Image context: the post's character, series (serie/sub-serie/copyright) and
artist tags are sent as "context_tags"; the translator puts them (plus its
characters.txt note for each character) into the LLM prompt.

Re-run control: a finished job tags the post machine_translated (plus
translation_incomplete when some blocks stayed untranslated). Tagged posts
are refused with 409; removing the tag allows a new run, which replaces all
[MT]/[MT?] notes and never touches unmarked (human) notes.

Restart semantics: pending jobs just stay queued; a job found "running" at
startup was interrupted, and is re-queued once (attempts < 2), else failed.
"""

import json
import logging
import re
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import sqlalchemy as sa
import sqlalchemy.exc
import sqlalchemy.orm.exc

from szurubooru import db, errors, model, rest
from szurubooru.func import cache, posts, snapshots, tags, users, versions

logger = logging.getLogger(__name__)

TRANSLATABLE_TYPES = (model.Post.TYPE_IMAGE, model.Post.TYPE_ANIMATION)
MAX_ATTEMPTS = 2  # first run + one retry after an interrupted run
MERGE_RETRIES = 5  # post version conflicts
QUEUE_FULL_RETRIES = 40  # translator 503 "queue full", ~30 s apart
MT_PREFIX_RE = re.compile(r"^\s*\[MT\??\]")  # [MT] or [MT?]
MT_PLACEHOLDER_RE = re.compile(r"^\s*\[MT\?\]")  # untranslated
# Speaker colour from the translator (text blocks that mix speakers by
# lettering colour): stored right after the prefix as "{c:#rrggbb}", which the
# client strips and turns into the note's border/tint. Strictly #rrggbb.
COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
# Re-run control: a post with TAG_DONE is not translated again (409) until
# someone removes the tag. TAG_INCOMPLETE marks runs that left [MT?] notes.
TAG_DONE = "machine_translated"
TAG_INCOMPLETE = "translation_incomplete"
# Tag category name -> context_tags role sent to the translator. Other
# categories (general, type, ...) are not sent.
CONTEXT_CATEGORIES = {
    "character": "character",
    "serie": "series",
    "series": "series",
    "sub-serie": "series",
    "copyright": "series",
    "artist": "artist",
}
# Placeholder tags that say nothing about the image.
CONTEXT_IGNORED_TAGS = {
    "original_character",
    "no_character",
    "other_or_unknown_artist",
    "unknown_artist",
}

_wake = threading.Event()
_worker_started = False


class TranslationNotConfiguredError(errors.ValidationError):
    pass


class InvalidTranslationPostTypeError(errors.ValidationError):
    pass


class TranslationJobAlreadyActiveError(errors.IntegrityError):
    pass


class PostAlreadyTranslatedError(errors.IntegrityError):
    pass


class TranslationError(RuntimeError):
    """A job failure whose message is shown to the user as-is."""


# --- configuration ---------------------------------------------------------


def _api_key() -> str:
    return os.getenv("TRANSLATE_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(_api_key())


def _translator_url() -> str:
    return os.getenv("TRANSLATE_URL", "http://translate:8765").rstrip("/")


def _image_base() -> str:
    return os.getenv("TRANSLATE_IMAGE_BASE", "http://client/data/")


def _use_llm() -> bool:
    return os.getenv("TRANSLATE_LLM", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _timeout() -> float:
    try:
        return float(os.getenv("TRANSLATE_TIMEOUT", "900"))
    except ValueError:
        return 900.0


# --- API side --------------------------------------------------------------


def get_latest_job(post_id: int) -> Optional[model.TranslationJob]:
    return (
        db.session.query(model.TranslationJob)
        .filter(model.TranslationJob.post_id == post_id)
        .order_by(model.TranslationJob.job_id.desc())
        .first()
    )


def _get_active_job(post_id: int) -> Optional[model.TranslationJob]:
    return (
        db.session.query(model.TranslationJob)
        .filter(model.TranslationJob.post_id == post_id)
        .filter(
            model.TranslationJob.status.in_(
                model.TranslationJob.ACTIVE_STATUSES
            )
        )
        .first()
    )


def create_job(
    post: model.Post, user: Optional[model.User]
) -> model.TranslationJob:
    if not is_configured():
        raise TranslationNotConfiguredError(
            "Translation is not configured on this server."
        )
    if post.type not in TRANSLATABLE_TYPES:
        raise InvalidTranslationPostTypeError(
            "Only image and GIF posts can be translated."
        )
    if has_tag(post, TAG_DONE):
        raise PostAlreadyTranslatedError(
            "This post is already machine-translated. Remove the %r tag "
            "to translate it again." % TAG_DONE
        )
    if _get_active_job(post.post_id):
        raise TranslationJobAlreadyActiveError(
            "A translation is already in progress for this post."
        )
    job = model.TranslationJob()
    job.post_id = post.post_id
    job.user = user if user and user.user_id else None
    job.status = model.TranslationJob.STATUS_PENDING
    job.attempts = 0
    job.creation_time = datetime.utcnow()
    db.session.add(job)
    try:
        db.session.flush()
    except sa.exc.IntegrityError:
        # The partial unique index caught a concurrent request.
        db.session.rollback()
        raise TranslationJobAlreadyActiveError(
            "A translation is already in progress for this post."
        )
    return job


def wake_worker() -> None:
    _wake.set()


def _queue_position(job: model.TranslationJob) -> Optional[int]:
    """Number of jobs that will be processed before this one."""
    if job.status != model.TranslationJob.STATUS_PENDING:
        return None
    return (
        db.session.query(sa.func.count(model.TranslationJob.job_id))
        .filter(
            model.TranslationJob.status.in_(
                model.TranslationJob.ACTIVE_STATUSES
            )
        )
        .filter(model.TranslationJob.job_id < job.job_id)
        .scalar()
    )


def serialize_job(
    job: Optional[model.TranslationJob], auth_user: model.User
) -> rest.Response:
    if not job:
        return {"status": None}
    return {
        "id": job.job_id,
        "postId": job.post_id,
        "status": job.status,
        "error": job.error,
        "user": users.serialize_micro_user(job.user, auth_user),
        "attempts": job.attempts,
        "notesAdded": job.notes_added,
        "notesReplaced": job.notes_replaced,
        "notesSkipped": job.notes_skipped,
        "queuePosition": _queue_position(job),
        "creationTime": job.creation_time,
        "startTime": job.start_time,
        "endTime": job.end_time,
    }


# --- note merging (pure) ---------------------------------------------------


def _clean_polygon(polygon: Any) -> Optional[List[List[float]]]:
    if not isinstance(polygon, (list, tuple)) or len(polygon) < 3:
        return None
    ret = []
    for point in polygon:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            return None
        if x != x or y != y:  # NaN
            return None
        ret.append([min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)])
    return ret


def note_color(color: Any) -> Optional[str]:
    """The block's colour as lowercase #rrggbb, or None (anything else)."""
    if isinstance(color, str) and COLOR_RE.fullmatch(color):
        return color.lower()
    return None


def note_text(
    source: Optional[str],
    translation: Optional[str],
    color: Optional[str] = None,
) -> str:
    source = (source or "").strip()
    translation = (translation or "").strip() if translation else ""
    color = note_color(color)
    marker = "{c:%s}" % color if color else ""
    if translation:
        return "[MT]%s %s" % (marker, translation)
    # No translation: show the Japanese so the note isn't empty.
    return "[MT?]%s %s" % (marker, source)


def is_mt_note(text: str) -> bool:
    return bool(MT_PREFIX_RE.match(text or ""))


def is_mt_placeholder(text: str) -> bool:
    return bool(MT_PLACEHOLDER_RE.match(text or ""))


def has_tag(post: model.Post, name: str) -> bool:
    name = name.lower()
    return any(
        tag_name.name.lower() == name
        for tag in post.tags
        for tag_name in tag.names
    )


def context_tags(post: model.Post) -> Dict[str, List[str]]:
    """{"character": [...], "series": [...], "artist": [...]} (primary tag
    names, sorted; empty roles left out) for the translator's prompt."""
    ret: Dict[str, List[str]] = {}
    for tag in post.tags:
        role = CONTEXT_CATEGORIES.get(
            (tag.category.name if tag.category else "").lower()
        )
        if not role or not tag.names:
            continue
        name = tag.names[0].name
        if name.lower() in CONTEXT_IGNORED_TAGS:
            continue
        ret.setdefault(role, []).append(name)
    return {role: sorted(names) for role, names in ret.items()}


def plan_notes(
    existing: List[Tuple[Any, str]], blocks: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[int], int]:
    """Return (notes to add, indexes of existing notes to remove, skipped).

    A run (first or forced by removing the machine_translated tag) replaces
    ALL existing [MT] and [MT?] notes with the new result. Notes without a
    marker (human-written) are never touched. Unusable blocks (bad polygon,
    no text at all) are skipped.
    """
    to_remove = [
        index for index, (_poly, text) in enumerate(existing)
        if is_mt_note(text)
    ]
    to_add = []
    skipped = 0
    for block in blocks or []:
        if not isinstance(block, dict):
            skipped += 1
            continue
        polygon = _clean_polygon(block.get("polygon"))
        source = block.get("source")
        translation = block.get("translation")
        if not isinstance(source, str):
            source = None
        if not isinstance(translation, str):
            translation = None
        if polygon is None or not ((source or "").strip() or translation):
            skipped += 1
            continue
        to_add.append(
            {
                "polygon": polygon,
                "text": note_text(source, translation, block.get("color")),
            }
        )
    return to_add, to_remove, skipped


def _update_tags(post: model.Post, incomplete: bool) -> List[model.Tag]:
    """Add machine_translated (+ translation_incomplete if needed), remove
    translation_incomplete after a complete run. Returns created tags."""
    wanted = [TAG_DONE] + ([TAG_INCOMPLETE] if incomplete else [])
    missing = [name for name in wanted if not has_tag(post, name)]
    new_tags = []
    if missing:
        existing_tags, new_tags = tags.get_or_create_tags_by_names(missing)
        post.tags = post.tags + existing_tags + new_tags
    if not incomplete and has_tag(post, TAG_INCOMPLETE):
        post.tags = [
            tag for tag in post.tags
            if not any(
                n.name.lower() == TAG_INCOMPLETE for n in tag.names
            )
        ]
    return new_tags


# --- translator client -----------------------------------------------------


def _http_error_detail(ex: urllib.error.HTTPError) -> str:
    try:
        body = ex.read().decode("utf-8", "replace")
    except Exception:
        return ex.reason or ""
    try:
        detail = json.loads(body).get("detail")
        if detail:
            return str(detail)
    except (ValueError, AttributeError):
        pass
    return body[:300] or (ex.reason or "")


def call_translator(
    image_url: str, context: Optional[Dict[str, List[str]]] = None
) -> Dict[str, Any]:
    url = _translator_url() + "/translate"
    if not _use_llm():
        url += "?translate=false"
    body: Dict[str, Any] = {"image_url": image_url}
    if context:
        body["context_tags"] = context
    data = json.dumps(body).encode("utf-8")
    for _ in range(QUEUE_FULL_RETRIES):
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "X-API-Key": _api_key(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=_timeout()) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            if not isinstance(result, dict) or not isinstance(
                result.get("blocks"), list
            ):
                raise TranslationError(
                    "Translator returned an unexpected response."
                )
            return result
        except urllib.error.HTTPError as ex:
            if ex.code == 503:
                try:
                    delay = int(ex.headers.get("Retry-After", "30"))
                except ValueError:
                    delay = 30
                logger.info("Translator queue full, retrying in %ds", delay)
                time.sleep(min(max(delay, 5), 120))
                continue
            raise TranslationError(
                "Translator error (HTTP %d): %s"
                % (ex.code, _http_error_detail(ex))
            )
        except (socket.timeout, TimeoutError):
            raise TranslationError(
                "Translator did not answer within %d s." % _timeout()
            )
        except urllib.error.URLError as ex:
            if isinstance(ex.reason, socket.timeout):
                raise TranslationError(
                    "Translator did not answer within %d s." % _timeout()
                )
            raise TranslationError(
                "Translator is unreachable: %s" % (ex.reason,)
            )
        except ValueError:
            raise TranslationError("Translator returned invalid JSON.")
    raise TranslationError("Translator queue stayed full; try again later.")


# --- worker ----------------------------------------------------------------


def _get_job(job_id: int) -> Optional[model.TranslationJob]:
    return db.session.query(model.TranslationJob).get(job_id)


def _recover_interrupted_jobs() -> None:
    jobs = (
        db.session.query(model.TranslationJob)
        .filter(
            model.TranslationJob.status
            == model.TranslationJob.STATUS_RUNNING
        )
        .all()
    )
    for job in jobs:
        if job.attempts >= MAX_ATTEMPTS:
            job.status = model.TranslationJob.STATUS_FAILED
            job.error = "Interrupted by a server restart (already retried)."
            job.end_time = datetime.utcnow()
            logger.warning("Translation job %d failed: interrupted twice",
                           job.job_id)
        else:
            job.status = model.TranslationJob.STATUS_PENDING
            logger.warning("Translation job %d re-queued after restart",
                           job.job_id)
    db.session.commit()


def _claim_next_job() -> Optional[int]:
    job = (
        db.session.query(model.TranslationJob)
        .filter(
            model.TranslationJob.status
            == model.TranslationJob.STATUS_PENDING
        )
        .order_by(model.TranslationJob.job_id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not job:
        db.session.rollback()
        return None
    job.status = model.TranslationJob.STATUS_RUNNING
    job.attempts = (job.attempts or 0) + 1
    job.start_time = datetime.utcnow()
    job.error = None
    job_id = job.job_id
    db.session.commit()
    return job_id


def _fail_job(job_id: int, message: str) -> None:
    db.session.rollback()
    job = _get_job(job_id)
    if not job:
        return  # post (and so the job) was deleted meanwhile
    job.status = model.TranslationJob.STATUS_FAILED
    job.error = message[:2000]
    job.end_time = datetime.utcnow()
    db.session.commit()


def _store_result(job_id: int, result: Dict[str, Any]) -> None:
    """Keep the raw blocks (polygon, source, translation, ...) on the job
    for later use; they are not exposed by the API."""
    job = _get_job(job_id)
    if not job:
        db.session.rollback()
        return
    job.result = {
        "width": result.get("width"),
        "height": result.get("height"),
        "blocks": result["blocks"],
    }
    db.session.commit()


def _merge_result(job_id: int, post_id: int, blocks: List[Any]) -> None:
    for attempt in range(MERGE_RETRIES):
        try:
            job = _get_job(job_id)
            if not job:
                db.session.rollback()
                return
            post = db.session.query(model.Post).get(post_id)
            if not post:
                raise TranslationError("The post was deleted.")
            current = list(post.notes)
            existing = [(note.polygon, note.text) for note in current]
            to_add, to_remove, skipped = plan_notes(existing, blocks)
            incomplete = any(is_mt_placeholder(n["text"]) for n in to_add)
            for index in to_remove:
                post.notes.remove(current[index])  # delete-orphan
            for note in to_add:
                post.notes.append(
                    model.PostNote(polygon=note["polygon"], text=note["text"])
                )
            new_tags = _update_tags(post, incomplete)
            versions.bump_version(post)
            post.last_edit_time = datetime.utcnow()
            db.session.flush()
            for tag in new_tags:
                snapshots.create(tag, job.user)
            snapshots.modify(post, job.user)
            job.status = model.TranslationJob.STATUS_DONE
            job.notes_added = len(to_add)
            job.notes_replaced = len(to_remove)
            job.notes_skipped = skipped
            job.end_time = datetime.utcnow()
            db.session.commit()
            cache.purge()
            logger.info(
                "Translation job %d: post %d, %d notes added "
                "(%d old MT notes removed), %d skipped%s",
                job_id, post_id, len(to_add), len(to_remove), skipped,
                ", incomplete" if incomplete else "",
            )
            return
        except sa.orm.exc.StaleDataError:
            db.session.rollback()
            logger.info(
                "Translation job %d: post %d changed meanwhile, retrying",
                job_id, post_id,
            )
            time.sleep(0.5 * (attempt + 1))
    raise TranslationError(
        "The post kept changing while saving notes; please try again."
    )


def _process_job(job_id: int) -> None:
    job = _get_job(job_id)
    if not job:
        db.session.rollback()
        return
    post = job.post
    if post.type not in TRANSLATABLE_TYPES:
        raise TranslationError("Only image and GIF posts can be translated.")
    post_id = post.post_id
    image_url = _image_base().rstrip("/") + "/" + posts.get_post_content_path(
        post
    )
    context = context_tags(post)
    # Don't sit in an open transaction during the long translator call.
    db.session.commit()
    logger.info("Translation job %d: post %d -> %s, context %s", job_id,
                post_id, _translator_url(), context)
    result = call_translator(image_url, context)
    _store_result(job_id, result)
    _merge_result(job_id, post_id, result["blocks"])


def _run_one(job_id: int) -> None:
    try:
        _process_job(job_id)
    except TranslationError as ex:
        logger.warning("Translation job %d failed: %s", job_id, ex)
        _fail_job(job_id, str(ex))
    except Exception as ex:
        logger.exception("Translation job %d crashed", job_id)
        _fail_job(job_id, "Internal error: %s: %s" % (type(ex).__name__, ex))


def _worker_loop() -> None:
    while True:
        try:
            _recover_interrupted_jobs()
            break
        except Exception:
            logger.exception("Translation worker: recovery failed, retrying")
            db.session.remove()
            time.sleep(10)
        finally:
            db.session.remove()
    logger.info("Translation worker started (%s, llm=%s)",
                _translator_url(), _use_llm())
    while True:
        try:
            _wake.clear()
            job_id = _claim_next_job()
            if job_id is None:
                _wake.wait(timeout=10)
                continue
            _run_one(job_id)
        except Exception:
            logger.exception("Translation worker error")
            time.sleep(5)
        finally:
            db.session.remove()


def start_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    if not is_configured():
        logger.info("Translation worker disabled: TRANSLATE_API_KEY unset")
        return
    _worker_started = True
    threading.Thread(
        target=_worker_loop, name="translation-worker", daemon=True
    ).start()

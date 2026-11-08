from typing import Dict

from szurubooru import rest
from szurubooru.func import auth, posts, translation_jobs


def _get_post_id(params: Dict[str, str]) -> int:
    try:
        return int(params["post_id"])
    except (TypeError, ValueError):
        raise posts.InvalidPostIdError(
            "Invalid post ID: %r." % params["post_id"]
        )


@rest.routes.post("/post/(?P<post_id>[^/]+)/translate/?")
def create_translation_job(
    ctx: rest.Context, params: Dict[str, str]
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:edit:notes")
    post = posts.get_post_by_id(_get_post_id(params))
    job = translation_jobs.create_job(post, ctx.user)
    ctx.session.commit()
    translation_jobs.wake_worker()
    return translation_jobs.serialize_job(job, ctx.user)


@rest.routes.get("/post/(?P<post_id>[^/]+)/translate/?")
def get_translation_job(
    ctx: rest.Context, params: Dict[str, str]
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:edit:notes")
    post = posts.get_post_by_id(_get_post_id(params))
    job = translation_jobs.get_latest_job(post.post_id)
    return translation_jobs.serialize_job(job, ctx.user)

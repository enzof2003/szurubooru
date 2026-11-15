from typing import Dict

from szurubooru import rest
from szurubooru.func import auth, file_uploads, posts


@rest.routes.post("/uploads/?")
def create_temporary_file(
    ctx: rest.Context, _params: Dict[str, str] = {}
) -> rest.Response:
    auth.verify_privilege(ctx.user, "uploads:create")
    content = ctx.get_file(
        "content",
        allow_tokens=False,
        use_video_downloader=auth.has_privilege(
            ctx.user, "uploads:use_downloader"
        ),
    )
    # Convert here: /uploads is served via the direct (non-Cloudflare)
    # hostname, so a slow re-encode isn't cut off by Cloudflare's 100 s limit.
    content = posts.convert_mov_to_mp4_if_enabled(content)
    token = file_uploads.save(content)
    return {"token": token}

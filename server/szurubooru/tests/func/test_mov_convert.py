"""
DB-free tests for MOV -> MP4 conversion on upload.
Builds tiny QuickTime clips with ffmpeg's lavfi sources.
"""
import json
import subprocess
from types import SimpleNamespace

import pytest

from szurubooru import config, model
from szurubooru.func import images, mime, posts, util


def _make_mov(vcodec_args, acodec_args, size="64x48"):
    with util.create_temp_file_path(suffix=".mov") as path:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y"]
            + ["-f", "lavfi", "-i", "testsrc=d=1:s=%s:r=10" % size]
            + ["-f", "lavfi", "-i", "sine=duration=1"]
            + ["-timecode", "01:00:00:00"]
            + vcodec_args
            + acodec_args
            + ["-f", "mov", path],
            check=True,
        )
        with open(path, "rb") as handle:
            return handle.read()


def _probe(content):
    with util.create_temp_file_path(suffix=".mp4") as path:
        with open(path, "wb") as handle:
            handle.write(content)
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-of", "json",
                "-show_entries",
                "stream=codec_type,codec_name,pix_fmt,width,height",
                path,
            ],
            check=True,
            capture_output=True,
        ).stdout
    return json.loads(out)["streams"]


def _assert_browser_mp4(content):
    assert mime.get_mime_type(content) == "video/mp4"
    streams = _probe(content)
    # only one video + one audio stream: the timecode track is dropped
    assert [s["codec_type"] for s in streams] == ["video", "audio"]
    assert streams[0]["codec_name"] == "h264"
    assert streams[0]["pix_fmt"] == "yuv420p"
    assert streams[1]["codec_name"] == "aac"
    # +faststart: moov atom is before mdat
    assert content.find(b"moov") < content.find(b"mdat")
    return streams


def test_h264_mov_is_remuxed():
    mov = _make_mov(
        ["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:a", "aac"]
    )
    assert mime.get_mime_type(mov) == "video/quicktime"
    mp4 = images.Image(mov).mov_to_mp4()
    streams = _assert_browser_mp4(mp4)
    assert (streams[0]["width"], streams[0]["height"]) == (64, 48)
    # copied, not re-encoded: the H.264 bitstream is byte-identical
    src_mdat = mov[mov.find(b"mdat") :]
    first_frame = mp4[mp4.find(b"mdat") + 8 : mp4.find(b"mdat") + 200]
    assert first_frame in src_mdat


@pytest.mark.parametrize(
    "vcodec_args,acodec_args,size",
    [
        # MPEG-4 Part 2, odd dimensions, PCM audio
        (["-c:v", "mpeg4"], ["-c:a", "pcm_s16le"], "65x49"),
        # H.264 but 4:2:2 (not browser-playable), ALAC audio
        (
            ["-c:v", "libx264", "-pix_fmt", "yuv422p"],
            ["-c:a", "alac"],
            "64x48",
        ),
    ],
)
def test_non_browser_mov_is_reencoded(vcodec_args, acodec_args, size):
    mov = _make_mov(vcodec_args, acodec_args, size=size)
    mp4 = images.Image(mov).mov_to_mp4()
    streams = _assert_browser_mp4(mp4)
    assert (streams[0]["width"], streams[0]["height"]) == (64, 48)


class _NoDuplicates:
    def query(self, *_args):
        return self

    def filter(self, *_args):
        return self

    def one_or_none(self):
        return None


@pytest.mark.parametrize("enabled", [True, False])
def test_update_post_content_converts_mov(monkeypatch, enabled):
    monkeypatch.setitem(
        config.config, "convert", {"gif": {}, "mov": {"to_mp4": enabled}}
    )
    monkeypatch.setattr(posts.db, "session", _NoDuplicates())
    mov = _make_mov(
        ["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:a", "aac"]
    )
    post = SimpleNamespace(post_id=1)
    posts.update_post_content(post, mov)
    stored = getattr(post, "__content")
    assert post.type == model.Post.TYPE_VIDEO
    assert post.checksum == util.get_sha1(stored)
    assert post.file_size == len(stored)
    if enabled:
        assert post.mime_type == "video/mp4"
        _assert_browser_mp4(stored)
    else:
        assert post.mime_type == "video/quicktime"
        assert stored == mov


def test_update_post_content_rejects_unconvertible_mov(monkeypatch):
    monkeypatch.setitem(
        config.config, "convert", {"gif": {}, "mov": {"to_mp4": True}}
    )

    def fail(_self, **_kwargs):
        raise images.errors.ProcessingError("boom")

    monkeypatch.setattr(images.Image, "mov_to_mp4", fail)
    mov = _make_mov(
        ["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:a", "aac"]
    )
    with pytest.raises(posts.InvalidPostContentError):
        posts.update_post_content(SimpleNamespace(post_id=1), mov)


def _enable(monkeypatch, **mov):
    monkeypatch.setitem(
        config.config, "convert", {"gif": {}, "mov": {"to_mp4": True, **mov}}
    )


def test_video_pixel_frames():
    mov = _make_mov(["-c:v", "mpeg4"], ["-c:a", "aac"], size="64x48")
    # 64 * 48 px, 10 fps, ~1 s
    assert images.Image(mov).video_pixel_frames() == pytest.approx(
        64 * 48 * 10, rel=0.1
    )


def test_reencode_rejected_when_estimate_exceeds_limit(monkeypatch):
    _enable(monkeypatch, max_encode_seconds=480)
    # pretend the encoder does 1 px-frame/s: 64x48x10 -> ~8.5 hours
    monkeypatch.setattr(posts, "MOV_REENCODE_PIXEL_FRAMES_PER_SECOND", 1)
    mov = _make_mov(["-c:v", "mpeg4"], ["-c:a", "aac"])

    def must_not_encode(_self, **_kwargs):
        raise AssertionError("encoder should not run")

    monkeypatch.setattr(images.Image, "mov_to_mp4", must_not_encode)
    with pytest.raises(posts.InvalidPostContentError) as exc:
        posts.convert_mov_to_mp4_if_enabled(mov)
    assert "Please convert it to MP4" in str(exc.value)


def test_remux_never_rejected_by_estimate(monkeypatch):
    _enable(monkeypatch, max_encode_seconds=1)
    monkeypatch.setattr(posts, "MOV_REENCODE_PIXEL_FRAMES_PER_SECOND", 1)
    mov = _make_mov(
        ["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:a", "aac"]
    )
    _assert_browser_mp4(posts.convert_mov_to_mp4_if_enabled(mov))


def test_reencode_allowed_under_limit(monkeypatch):
    _enable(monkeypatch, max_encode_seconds=480)
    mov = _make_mov(["-c:v", "mpeg4"], ["-c:a", "aac"])
    _assert_browser_mp4(posts.convert_mov_to_mp4_if_enabled(mov))


def test_non_mov_and_missing_config_pass_through(monkeypatch):
    mov = _make_mov(
        ["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:a", "aac"]
    )
    monkeypatch.setattr(config, "config", {})
    assert posts.convert_mov_to_mp4_if_enabled(mov) == mov
    _enable(monkeypatch)
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
    assert posts.convert_mov_to_mp4_if_enabled(png) == png


def test_upload_api_converts_mov(monkeypatch):
    from szurubooru.api import upload_api

    _enable(monkeypatch)
    mov = _make_mov(
        ["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:a", "aac"]
    )
    saved = {}
    monkeypatch.setattr(upload_api.auth, "verify_privilege", lambda *a: None)
    monkeypatch.setattr(upload_api.auth, "has_privilege", lambda *a: False)
    monkeypatch.setattr(
        upload_api.file_uploads,
        "save",
        lambda content: saved.setdefault("content", content) and "tok",
    )
    ctx = SimpleNamespace(user=None, get_file=lambda *a, **k: mov)
    assert upload_api.create_temporary_file(ctx) == {"token": "tok"}
    _assert_browser_mp4(saved["content"])


# --- time limit and concurrency (the file's own metadata can't be trusted) --


def test_execute_kills_ffmpeg_on_timeout():
    mov = _make_mov(["-c:v", "mpeg4"], ["-c:a", "aac"])
    image = images.Image(mov)
    started = []
    real_popen = subprocess.Popen

    def popen(cli, **kwargs):
        proc = real_popen(["sleep", "30"], **kwargs)
        started.append(proc)
        return proc

    images.subprocess.Popen = popen
    try:
        with pytest.raises(images.ProcessingTimeoutError):
            image._execute(["-i", "{path}"], timeout=0.2)
    finally:
        images.subprocess.Popen = real_popen
    assert len(started) == 1
    assert started[0].returncode == -9  # killed and reaped, not left behind


def test_conversion_gets_limit_plus_margin_as_timeout(monkeypatch):
    _enable(monkeypatch, max_encode_seconds=100)
    seen = []
    monkeypatch.setattr(
        images.Image,
        "mov_to_mp4",
        lambda _self, timeout=None: seen.append(timeout) or b"mp4",
    )
    for vcodec in (["-c:v", "mpeg4"], ["-c:v", "libx264", "-pix_fmt", "yuv420p"]):
        mov = _make_mov(vcodec, ["-c:a", "aac"])
        assert posts.convert_mov_to_mp4_if_enabled(mov) == b"mp4"
    # re-encode and remux alike
    assert seen == [100 + posts.MOV_ENCODE_TIMEOUT_MARGIN] * 2


def test_unknown_duration_is_still_bounded_by_the_timeout(monkeypatch):
    _enable(monkeypatch, max_encode_seconds=100)
    monkeypatch.setattr(images.Image, "video_pixel_frames", lambda _self: None)

    def too_slow(_self, timeout=None):
        assert timeout
        raise images.ProcessingTimeoutError("Processing took longer...")

    monkeypatch.setattr(images.Image, "mov_to_mp4", too_slow)
    mov = _make_mov(["-c:v", "mpeg4"], ["-c:a", "aac"])
    with pytest.raises(posts.InvalidPostContentError) as exc:
        posts.convert_mov_to_mp4_if_enabled(mov)
    assert "took too long" in str(exc.value)


def test_reencode_refused_while_all_slots_are_busy(monkeypatch):
    _enable(monkeypatch, max_encode_seconds=480)
    monkeypatch.setattr(
        images.Image, "mov_to_mp4", lambda _self, timeout=None: b"mp4"
    )
    reencode = _make_mov(["-c:v", "mpeg4"], ["-c:a", "aac"])
    remux = _make_mov(
        ["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:a", "aac"]
    )
    slots = posts._mov_reencode_slots
    for _ in range(posts.MOV_MAX_CONCURRENT_REENCODES):
        assert slots.acquire(blocking=False)
    try:
        with pytest.raises(posts.InvalidPostContentError) as exc:
            posts.convert_mov_to_mp4_if_enabled(reencode)
        assert "busy" in str(exc.value)
        # remuxes are quick and never queue behind re-encodes
        assert posts.convert_mov_to_mp4_if_enabled(remux) == b"mp4"
    finally:
        for _ in range(posts.MOV_MAX_CONCURRENT_REENCODES):
            slots.release()
    # the slot is given back after a conversion, also a failed one
    assert posts.convert_mov_to_mp4_if_enabled(reencode) == b"mp4"

    def boom(_self, timeout=None):
        raise images.errors.ProcessingError("boom")

    monkeypatch.setattr(images.Image, "mov_to_mp4", boom)
    with pytest.raises(posts.InvalidPostContentError):
        posts.convert_mov_to_mp4_if_enabled(reencode)
    for _ in range(posts.MOV_MAX_CONCURRENT_REENCODES):
        assert slots.acquire(blocking=False)
    for _ in range(posts.MOV_MAX_CONCURRENT_REENCODES):
        slots.release()


# --- how often the upload is written to a temporary file -------------------


@pytest.mark.parametrize(
    "vcodec_args,expected",
    [
        (["-c:v", "libx264", "-pix_fmt", "yuv420p"], "h264/yuv420p"),
        (["-c:v", "mpeg4"], "mpeg4/yuv420p"),
        (["-c:v", "libx264", "-pix_fmt", "yuv444p"], "h264/yuv444p"),
    ],
)
def test_video_codec_from_loaded_info_matches_a_fresh_probe(
    vcodec_args, expected
):
    image = images.Image(_make_mov(vcodec_args, ["-c:a", "aac"]))
    assert image._video_codec() == expected
    assert image._video_codec() == image._first_stream_codec("v:0")


def test_video_codec_is_the_first_video_stream_even_after_audio():
    # audio stream first in the file: info lists video streams only
    with util.create_temp_file_path(suffix=".mov") as path:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y"]
            + ["-f", "lavfi", "-i", "sine=duration=1"]
            + ["-f", "lavfi", "-i", "testsrc=d=1:s=64x48:r=10"]
            + ["-map", "0:a", "-map", "1:v", "-c:v", "mpeg4", "-c:a", "aac"]
            + ["-f", "mov", path],
            check=True,
        )
        with open(path, "rb") as handle:
            mov = handle.read()
    image = images.Image(mov)
    assert image._video_codec() == image._first_stream_codec("v:0")
    assert image._video_codec().startswith("mpeg4/")


@pytest.mark.parametrize(
    "vcodec_args", [["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:v", "mpeg4"]]
)
def test_conversion_writes_the_upload_three_times_not_five(
    monkeypatch, vcodec_args
):
    """Every ffprobe/ffmpeg run writes the whole upload to a temp file: one
    probe when the file is loaded, one for the audio codec, one encode."""
    _enable(monkeypatch)
    mov = _make_mov(vcodec_args, ["-c:a", "aac"])
    runs = []
    real_execute = images.Image._execute

    def counting(self, cli, program="ffmpeg", **kwargs):
        runs.append(program)
        return real_execute(self, cli, program=program, **kwargs)

    monkeypatch.setattr(images.Image, "_execute", counting)
    _assert_browser_mp4(posts.convert_mov_to_mp4_if_enabled(mov))
    assert runs == ["ffprobe", "ffprobe", "ffmpeg"]

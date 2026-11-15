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

    def fail(_self):
        raise images.errors.ProcessingError("boom")

    monkeypatch.setattr(images.Image, "mov_to_mp4", fail)
    mov = _make_mov(
        ["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:a", "aac"]
    )
    with pytest.raises(posts.InvalidPostContentError):
        posts.update_post_content(SimpleNamespace(post_id=1), mov)

import json
import logging
import math
import re
import shlex
import subprocess
from io import BytesIO
from typing import List, Optional

import HeifImagePlugin
import pillow_avif
from PIL import Image as PILImage

from szurubooru import errors
from szurubooru.func import mime, util

logger = logging.getLogger(__name__)


def convert_heif_to_png(content: bytes) -> bytes:
    img = PILImage.open(BytesIO(content))
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format="PNG")
    return img_byte_arr.getvalue()


class ProcessingTimeoutError(errors.ProcessingError):
    pass


class Image:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self._reload_info()

    @property
    def width(self) -> int:
        return self.info["streams"][0]["width"]

    @property
    def height(self) -> int:
        return self.info["streams"][0]["height"]

    @property
    def frames(self) -> int:
        return self.info["streams"][0]["nb_read_frames"]

    def resize_fill(self, width: int, height: int) -> None:
        width_greater = self.width > self.height
        width, height = (-1, height) if width_greater else (width, -1)

        cli = [
            "-i",
            "{path}",
            "-f",
            "image2",
            "-filter:v",
            "scale='{width}:{height}'".format(width=width, height=height),
            "-map",
            "0:v:0",
            "-vframes",
            "1",
            "-vcodec",
            "png",
            "-",
        ]
        if (
            "duration" in self.info["format"]
            and self.info["format"]["format_name"] != "swf"
        ):
            duration = float(self.info["format"]["duration"])
            if duration > 3:
                cli = [
                    "-ss",
                    "%d" % math.floor(duration * 0.3),
                ] + cli
        content = self._execute(cli, ignore_error_if_data=True)
        if not content:
            raise errors.ProcessingError("Error while resizing image.")
        self.content = content
        self._reload_info()

    def to_png(self) -> bytes:
        return self._execute(
            [
                "-i",
                "{path}",
                "-f",
                "image2",
                "-map",
                "0:v:0",
                "-vframes",
                "1",
                "-vcodec",
                "png",
                "-",
            ]
        )

    def to_jpeg(self) -> bytes:
        return self._execute(
            [
                "-f",
                "lavfi",
                "-i",
                "color=white:s=%dx%d" % (self.width, self.height),
                "-i",
                "{path}",
                "-f",
                "image2",
                "-filter_complex",
                "overlay",
                "-map",
                "0:v:0",
                "-vframes",
                "1",
                "-vcodec",
                "mjpeg",
                "-",
            ]
        )

    def to_webm(self) -> bytes:
        with util.create_temp_file_path(suffix=".log") as phase_log_path:
            # Pass 1
            self._execute(
                [
                    "-i",
                    "{path}",
                    "-pass",
                    "1",
                    "-passlogfile",
                    phase_log_path,
                    "-vcodec",
                    "libvpx-vp9",
                    "-crf",
                    "4",
                    "-b:v",
                    "2500K",
                    "-acodec",
                    "libvorbis",
                    "-f",
                    "webm",
                    "-y",
                    "/dev/null",
                ]
            )

            # Pass 2
            return self._execute(
                [
                    "-i",
                    "{path}",
                    "-pass",
                    "2",
                    "-passlogfile",
                    phase_log_path,
                    "-vcodec",
                    "libvpx-vp9",
                    "-crf",
                    "4",
                    "-b:v",
                    "2500K",
                    "-acodec",
                    "libvorbis",
                    "-f",
                    "webm",
                    "-",
                ]
            )

    def to_mp4(self) -> bytes:
        with util.create_temp_file_path(suffix=".dat") as mp4_temp_path:
            width = self.width
            height = self.height
            altered_dimensions = False

            if self.width % 2 != 0:
                width = self.width - 1
                altered_dimensions = True

            if self.height % 2 != 0:
                height = self.height - 1
                altered_dimensions = True

            args = [
                "-i",
                "{path}",
                "-vcodec",
                "libx264",
                "-preset",
                "slow",
                "-crf",
                "22",
                "-b:v",
                "200K",
                "-profile:v",
                "main",
                "-pix_fmt",
                "yuv420p",
                "-acodec",
                "aac",
                "-f",
                "mp4",
            ]

            if altered_dimensions:
                args += ["-filter:v", "scale='%d:%d'" % (width, height)]

            self._execute(args + ["-y", mp4_temp_path])

            with open(mp4_temp_path, "rb") as mp4_temp:
                return mp4_temp.read()

    def _first_stream_codec(self, selector: str) -> Optional[str]:
        streams = json.loads(
            self._execute(
                [
                    "-i",
                    "{path}",
                    "-of",
                    "json",
                    "-select_streams",
                    selector,
                    "-show_entries",
                    "stream=codec_name,pix_fmt",
                ],
                program="ffprobe",
            ).decode("utf-8")
        ).get("streams", [])
        if not streams:
            return None
        return "%s/%s" % (
            streams[0].get("codec_name"),
            streams[0].get("pix_fmt", ""),
        )

    def _video_codec(self) -> str:
        """
        "codec/pix_fmt" of the first video stream, from the probe already
        done when the file was loaded (self.info lists the video streams
        only, in order, so [0] is v:0). Every extra ffprobe run would write
        the whole upload to a temporary file again.
        """
        stream = self.info["streams"][0]
        return "%s/%s" % (stream.get("codec_name"), stream.get("pix_fmt", ""))

    def mov_needs_video_reencode(self, video: Optional[str] = None) -> bool:
        """Only H.264 4:2:0 plays everywhere; anything else is re-encoded."""
        if video is None:
            video = self._video_codec()
        return video != "h264/yuv420p"

    def video_pixel_frames(self) -> Optional[float]:
        """
        width * height * frame count of the first video stream, estimated
        from duration and average frame rate; None if they're unknown.
        """
        try:
            stream = self.info["streams"][0]
            duration = float(
                self.info["format"].get("duration") or stream["duration"]
            )
            num, den = stream["avg_frame_rate"].split("/")
            fps = float(num) / float(den)
            pixels = int(stream["width"]) * int(stream["height"])
        except (KeyError, IndexError, ValueError, ZeroDivisionError):
            return None
        if duration <= 0 or fps <= 0 or pixels <= 0:
            return None
        return pixels * fps * duration

    def mov_to_mp4(self, timeout: Optional[float] = None) -> bytes:
        """
        `timeout` (seconds): ffmpeg is killed and ProcessingTimeoutError
        raised when the conversion takes longer.

        Turn a QuickTime file into a browser-playable MP4.
        H.264 4:2:0 video is copied as-is (remux); anything else is
        re-encoded with libx264. AAC audio is copied, other audio is
        re-encoded to AAC. Only the first video and audio streams are
        kept; QuickTime timecode/data tracks are dropped.
        """
        video = self._video_codec()
        audio = self._first_stream_codec("a:0")
        copy_video = not self.mov_needs_video_reencode(video)
        copy_audio = audio is None or audio.startswith("aac/")
        with util.create_temp_file_path(suffix=".mp4") as mp4_temp_path:
            args = ["-i", "{path}", "-map", "0:v:0", "-map", "0:a:0?"]
            if copy_video:
                args += ["-c:v", "copy"]
            else:
                args += [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    # yuv420p needs even dimensions
                    "-vf",
                    "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                ]
            args += ["-c:a", "copy"] if copy_audio else ["-c:a", "aac"]
            args += [
                "-write_tmcd",
                "0",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                "-y",
                mp4_temp_path,
            ]
            logger.info(
                "Converting MOV to MP4 (video=%s %s, audio=%s %s)",
                video,
                "copy" if copy_video else "re-encode",
                audio,
                "copy" if copy_audio else "re-encode",
            )
            self._execute(args, timeout=timeout)
            with open(mp4_temp_path, "rb") as mp4_temp:
                return mp4_temp.read()

    def check_for_sound(self) -> bool:
        audioinfo = json.loads(
            self._execute(
                [
                    "-i",
                    "{path}",
                    "-of",
                    "json",
                    "-select_streams",
                    "a",
                    "-show_streams",
                ],
                program="ffprobe",
            ).decode("utf-8")
        )
        assert "streams" in audioinfo
        if len(audioinfo["streams"]) < 1:
            return False

        log = self._execute(
            [
                "-hide_banner",
                "-progress",
                "-",
                "-i",
                "{path}",
                "-af",
                "volumedetect",
                "-max_muxing_queue_size",
                "99999",
                "-vn",
                "-sn",
                "-f",
                "null",
                "-y",
                "/dev/null",
            ],
            get_logs=True,
        ).decode("utf-8", errors="replace")
        log_match = re.search(r".*volumedetect.*mean_volume: (.*) dB", log)
        if not log_match or not log_match.groups():
            raise errors.ProcessingError(
                "A problem occured when trying to check for audio"
            )
        meanvol = float(log_match.groups()[0])

        # -91.0 dB is the minimum for 16-bit audio, assume sound if > -80.0 dB
        return meanvol > -80.0

    def _execute(
        self,
        cli: List[str],
        program: str = "ffmpeg",
        ignore_error_if_data: bool = False,
        get_logs: bool = False,
        timeout: Optional[float] = None,
    ) -> bytes:
        mime_type = mime.get_mime_type(self.content)
        if mime.is_heif(mime_type):
            # FFmpeg does not support HEIF.
            # https://trac.ffmpeg.org/ticket/6521
            self.content = convert_heif_to_png(self.content)
        extension = mime.get_extension(mime_type)
        assert extension
        with util.create_temp_file(suffix="." + extension) as handle:
            handle.write(self.content)
            handle.flush()
            cli = [program, "-loglevel", "32" if get_logs else "24"] + cli
            cli = [part.format(path=handle.name) for part in cli]
            proc = subprocess.Popen(
                cli,
                stdout=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # the file's own metadata can't be trusted to bound the
                # work: stop ffmpeg rather than hold a request thread
                proc.kill()
                proc.communicate()
                logger.warning(
                    "Killed %s after %d s (cli=%r)",
                    program,
                    timeout,
                    " ".join(shlex.quote(arg) for arg in cli),
                )
                raise ProcessingTimeoutError(
                    "Processing took longer than %d seconds." % timeout
                )
            if proc.returncode != 0:
                logger.warning(
                    "Failed to execute ffmpeg command (cli=%r, err=%r)",
                    " ".join(shlex.quote(arg) for arg in cli),
                    err,
                )
                if (len(out) > 0 and not ignore_error_if_data) or len(
                    out
                ) == 0:
                    raise errors.ProcessingError(
                        "Error while processing image.\n" + err.decode("utf-8")
                    )
            return err if get_logs else out

    def _reload_info(self) -> None:
        self.info = json.loads(
            self._execute(
                [
                    "-i",
                    "{path}",
                    "-of",
                    "json",
                    "-select_streams",
                    "v",
                    "-show_format",
                    "-show_streams",
                ],
                program="ffprobe",
            ).decode("utf-8")
        )
        assert "format" in self.info
        assert "streams" in self.info
        if len(self.info["streams"]) < 1:
            logger.warning("The video contains no video streams.")
            raise errors.ProcessingError(
                "The video contains no video streams."
            )

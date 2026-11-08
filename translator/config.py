"""Settings loaded from .env (next to this file). Import this first: it pins thread counts."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

THREADS = max(1, int(os.getenv("THREADS", "4")))
# Pin native thread pools before numpy / onnxruntime are imported.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, str(THREADS))


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "on", "yes", "y"}


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name) or default)
    return p if p.is_absolute() else ROOT / p


def _list(name: str, default: str) -> tuple[str, ...]:
    return tuple(x.strip().lower() for x in os.getenv(name, default).split(",") if x.strip())


def _thresholds(name: str, default: str) -> dict[str, float]:
    out = {}
    for item in os.getenv(name, default).split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip().lower()] = float(v)
    return out


_EXE = ".exe" if sys.platform == "win32" else ""


@dataclass(frozen=True)
class Settings:
    # ---- HTTP service
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", ""))
    host: str = field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8765")))
    max_image_mb: float = field(default_factory=lambda: float(os.getenv("MAX_IMAGE_MB", "40")))
    allowed_url_hosts: tuple[str, ...] = field(default_factory=lambda: _list("ALLOWED_URL_HOSTS", ""))
    max_queue: int = field(default_factory=lambda: int(os.getenv("MAX_QUEUE", "16")))
    nice: int = field(default_factory=lambda: int(os.getenv("NICE", "10")))
    warmup: bool = field(default_factory=lambda: _bool("WARMUP", True))  # tiny detect + OCR at startup

    # ---- CPU budget shared by detection and OCR
    threads: int = THREADS

    # ---- Detection: Koharu Layout RF-DETR Seg 2XL (ONNX)
    det_model: Path = field(default_factory=lambda: _path("DET_MODEL", "models/rfdetr/rfdetr-seg-2xlarge-nomask.onnx"))
    det_thresholds: dict = field(default_factory=lambda: _thresholds(
        "DET_THRESHOLDS", "text=0.25,onomatopoeia=0.2,bubble=0.5,panel=0.5"))
    skip_classes: tuple[str, ...] = field(default_factory=lambda: _list("SKIP_CLASSES", "onomatopoeia"))

    # ---- OCR: PaddleOCR-VL 1.6 behind llama-server
    # managed = the service starts/stops its own llama-server; external = use OCR_BASE_URL as is.
    ocr_server: str = field(default_factory=lambda: os.getenv("OCR_SERVER", "managed").lower())
    ocr_base_url: str = field(default_factory=lambda: os.getenv("OCR_BASE_URL", "http://127.0.0.1:8081/v1").rstrip("/"))
    ocr_api_key: str = field(default_factory=lambda: os.getenv("OCR_API_KEY", ""))
    ocr_bin: Path = field(default_factory=lambda: _path("OCR_SERVER_BIN", f"tools/llama.cpp-cpu/llama-server{_EXE}"))
    ocr_model: Path = field(default_factory=lambda: _path("OCR_MODEL", "models/paddleocr-vl/PaddleOCR-VL-1.6-GGUF.gguf"))
    ocr_mmproj: Path = field(default_factory=lambda: _path("OCR_MMPROJ", "models/paddleocr-vl/PaddleOCR-VL-1.6-GGUF-mmproj.gguf"))
    ocr_port: int = field(default_factory=lambda: int(os.getenv("OCR_PORT", "8081")))
    ocr_gpu_layers: int = field(default_factory=lambda: int(os.getenv("OCR_GPU_LAYERS", "0")))
    ocr_max_pixels: int = field(default_factory=lambda: int(os.getenv("OCR_MAX_PIXELS", "0")))  # 0 = no downscale
    ocr_pad: float = field(default_factory=lambda: float(os.getenv("OCR_PAD", "0.01")))  # crop margin, x longest side
    ocr_timeout: float = field(default_factory=lambda: float(os.getenv("OCR_TIMEOUT", "180")))

    # ---- LLM (any OpenAI-compatible /chat/completions endpoint)
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gemma-4-26b-a4b-it"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    vision: bool = field(default_factory=lambda: _bool("VISION", True))
    reasoning: bool = field(default_factory=lambda: _bool("REASONING", False))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.3")))
    llm_max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "4096")))
    llm_timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "300")))
    llm_json_mode: bool = field(default_factory=lambda: _bool("LLM_JSON_MODE", True))
    llm_extra_body: dict = field(default_factory=lambda: json.loads(os.getenv("LLM_EXTRA_BODY", "") or "{}"))
    vision_max_side: int = field(default_factory=lambda: int(os.getenv("VISION_MAX_SIDE", "1536")))
    target_language: str = field(default_factory=lambda: os.getenv("TARGET_LANGUAGE", "English"))
    instructions_file: Path = field(default_factory=lambda: _path("INSTRUCTIONS_FILE", "instructions.txt"))
    glossary_file: Path = field(default_factory=lambda: _path("GLOSSARY_FILE", "glossary.txt"))


def load() -> Settings:
    return Settings()

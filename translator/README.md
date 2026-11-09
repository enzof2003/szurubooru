# manga-translate service

Small HTTP service for szurubooru: manga page in → notes out (box, Japanese OCR, English translation).
Test results and timings: see `RESULTS.md`. No PyTorch at runtime.

```
detect  Koharu Layout RF-DETR Seg 2XL (ONNX Runtime, CPU): text / onomatopoeia / bubble / panel
group   text boxes in the same bubble -> one note; onomatopoeia is skipped unless >= 90% inside a bubble
order   panels right->left, top->bottom, then notes inside each panel
ocr     PaddleOCR-VL 1.6 in a CPU llama-server (started and stopped by the service), one call per note
filter  drop notes with no Japanese
llm     one OpenAI-compatible chat call per page (OpenRouter on the VPS; local Gemma for tests);
        segments it drops or leaves empty get one retry on their own, then translation=null
```

## Files

| File | Purpose |
|---|---|
| `app.py` | FastAPI service (`POST /translate`, `GET /health`); one request at a time, FIFO queue |
| `pipeline.py` | detection, grouping, reading order, OCR server + client, Latin filter, LLM call |
| `childproc.py` | starts the OCR server so the OS kills it if the service dies (Windows job object, Linux PDEATHSIG) |
| `config.py` | reads `.env` |
| `client.py` | CLI: send images, print JSON or save `<name>.json` + `<name>_preview.png` |
| `render.py` | preview rendering |
| `bench.py` | in-process detection + OCR timing and peak RAM (service + OCR server) |
| `start_llm.ps1` | local Gemma 4 26B A4B llama-server (test stand-in for OpenRouter) |
| `tools/export_rfdetr_onnx.py` | rebuilds `models/rfdetr/*.onnx` from `model.safetensors` (dev extras only) |
| `instructions.txt`, `glossary.txt` | injected into the system prompt (`JP = EN` per glossary line) |
| `.env` / `.env.example` | settings |

Models (copy these to the VPS, ~1.97 GB):
`models/rfdetr/rfdetr-seg-2xlarge-nomask.onnx` (144 MB) and
`models/paddleocr-vl/PaddleOCR-VL-1.6-GGUF.gguf` + `PaddleOCR-VL-1.6-GGUF-mmproj.gguf` (1.8 GB).

## Run (Windows, this machine)

```powershell
# 1. service (starts its own CPU OCR server on OCR_PORT)
.venv\Scripts\python app.py
# 2. local LLM, only when not using OpenRouter (requires LOCAL_LLM_API_KEY)
powershell -ExecutionPolicy Bypass -File start_llm.ps1
# 3. use it
.venv\Scripts\python client.py translate-test\zwrazwrazw.jpeg
.venv\Scripts\python client.py --save "translate-test\*"
```

The OCR `llama-server` cannot outlive the service: Ctrl+C / SIGTERM stop it cleanly (lifespan + atexit),
and on a crash or hard kill (`taskkill /F`, `kill -9`) the OS kills it (Windows job object with
kill-on-close; Linux `PR_SET_PDEATHSIG`). At startup the service refuses to run if `OCR_PORT` is already in use.

## `.env` keys that matter

| Key | Default | Meaning |
|---|---|---|
| `API_KEY` | — | required `X-API-Key` header for `/translate` |
| `THREADS` | 4 | ONNX Runtime threads, OCR server `-t/-tb`, OMP/MKL |
| `MAX_QUEUE` | 16 | requests waiting behind the running one; beyond that → 503 + `Retry-After` |
| `NICE` | 10 | Linux CPU priority (inherited by the OCR server) |
| `WARMUP` | on | one tiny detection + OCR call at startup so the first page is not slow (cold model pages); `/health` reports `"warming"` until it is done |
| `DET_MODEL` | `models/rfdetr/rfdetr-seg-2xlarge-nomask.onnx` | detector (mask head pruned, identical boxes) |
| `DET_THRESHOLDS` | `text=0.25,onomatopoeia=0.2,bubble=0.5,panel=0.5` | per-class score thresholds |
| `SKIP_CLASSES` | `onomatopoeia` | not OCR'd/translated, listed in `meta.skipped` (except onomatopoeia ≥ 90% inside a bubble) |
| `SPLIT_BY_COLOR` | on | a block whose columns/lines have clearly different text colours (several speakers in one block) becomes one block per colour run, with its measured `color` and a speaker letter for the LLM (`colorsplit.py`) |
| `OCR_SERVER` | `managed` | `managed` = service runs `OCR_SERVER_BIN`; `external` = use `OCR_BASE_URL` |
| `OCR_SERVER_BIN` | `tools/llama.cpp-cpu/llama-server[.exe]` | CPU llama.cpp build |
| `OCR_PAD` | 0.01 | crop margin, fraction of the crop's longest side |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | local Gemma | OpenAI-compatible endpoint |

## API

All `/translate` calls need the header `X-API-Key: <API_KEY from .env>`.

```
POST /translate                 multipart/form-data, field "image"
POST /translate                 application/json {"image_url": "https://..."}
POST /translate?translate=false detection + OCR only (no LLM call)
GET  /health                    {"status": "ok", "busy": false, "queued": 0}  (no auth)
                                status: loading -> warming -> ok ("degraded" if the OCR server died)
```

Response: `{"width", "height", "blocks": [{"polygon", "bbox_px", "source", "translation"}], "meta"}`.
Blocks split by speaker colour also carry `"color": "#rrggbb"`: the measured text colour, boosted for
display (same hue, HSL S ≥ 60%, L 45–60%; the raw value is in `meta.notes[].measured_color`).
`translation` is `null` (never `""`) when the LLM gave nothing for that block even after the retry;
such blocks are also listed in `meta.untranslated` (`[{"id", "source"}]`, empty list when complete).
`polygon` is normalized to 0..1 (szurubooru note format); for a bubble it is the bubble's box.
`meta` has `timings_ms`, `threads`, `notes` (kind, text box, parts, `origin` when an onomatopoeia
box was promoted to text), `skipped` (boxes with their class and reason) and `llm` (usage, `attempts`, `retried_ids`, final `missing_ids`, `retry_error` if the retry failed).

Errors: 401 bad key, 400 bad input, 413 too large, 415 wrong content type, 502 LLM failure,
503 queue full.

## Switching to OpenRouter

Only `.env` changes:

```
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=google/gemma-4-26b-a4b-it
LLM_API_KEY=sk-or-...
```

Reasoning is disabled per request with both `reasoning: {"enabled": false}` (OpenRouter) and
`chat_template_kwargs: {"enable_thinking": false}` (llama-server). If a provider rejects
`response_format`, the request is retried without it. (Not yet tested against OpenRouter itself.)

## Linux VPS (CPU only, no torch)

RAM: peak ~5.0 GB combined while a page is being detected (service ~3.1 GB + OCR server ~1.9 GB);
~2.1 GB between requests (service ~0.26 GB + OCR server ~1.9 GB). The peak does not depend on page
size (detection always runs at 1152 px). Fits in 8 GB free with ~3 GB headroom.

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt          # no torch: onnxruntime + pillow + numpy + fastapi
# copy models/rfdetr/rfdetr-seg-2xlarge-nomask.onnx and models/paddleocr-vl/*.gguf from this machine

# CPU llama-server for the OCR: the Ubuntu x64 CPU archive of a llama.cpp release
# (tested build: b12189), unpacked to tools/llama.cpp-cpu/, or build it from source:
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
cmake -B build -DGGML_NATIVE=ON && cmake --build build --target llama-server -j
cp build/bin/llama-server ../tools/llama.cpp-cpu/ && cd ..

cp .env.example .env   # set API_KEY, THREADS=<cores to give it>, the OpenRouter LLM_* settings
.venv/bin/python app.py
```

Keep `HOST=127.0.0.1` and put nginx/Caddy (TLS) in front if szurubooru is not on the same host.
If szurubooru sends `image_url`s that point at localhost or a private IP, add that host to `ALLOWED_URL_HOSTS`.
Without an allowlist, private addresses are refused to prevent SSRF.

### Docker (alternative)

```bash
git clone <private repo> manga-translate && cd manga-translate
scripts/fetch_models.sh                          # PaddleOCR-VL GGUF + mmproj from Hugging Face (pinned, SHA-256 checked)
# + copy models/rfdetr/rfdetr-seg-2xlarge-nomask.onnx from the dev machine (not downloadable as ONNX)
cp .env.example .env                             # set API_KEY, THREADS, LLM_* (OpenRouter)
docker build -t manga-translate .                # builds llama-server b12189 for this CPU; no torch
docker run -d --init --restart unless-stopped --name manga-translate   -v "$PWD/.env:/app/.env:ro" -v "$PWD/models:/app/models:ro" -p 127.0.0.1:8765:8765 manga-translate
```

`scripts/fetch_models.sh` also works for the non-Docker install above.

Dev only (re-export the detector, benchmarks): `pip install -r requirements-dev.txt` (torch, rfdetr, onnx, psutil).

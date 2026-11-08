# Koharu as a translation endpoint for szurubooru: findings

Date: 2026-11-07. Installed Koharu version: **0.83.5** (`koharu.exe --version`, file version 0.83.5, built 2026-09-22).

## TL;DR

**Koharu 0.83.5 does not expose an HTTP API or an MCP server, so it cannot be used as a translation endpoint.**

- The HTTP API (`/api/v1/...`), the MCP server (`/mcp`), `--port`, `--host` and `--headless` all existed in older releases.
- They were removed in **0.63.0 (2026-08-11)** when the app was rebuilt on Tauri + CEF.
- The current UI talks to the Rust backend through in-process Tauri IPC only. No TCP or UDP port is opened.

As a result:

- `koharu_client.py` was **not** built, because there is nothing for it to call. As you asked, the alternative pipeline is proposed below but **not implemented**.
- The test folder `C:\Users\Enzof\translate-test\` **does not exist** on this machine, so no `<name>.json` outputs were produced. Your existing project `Documents\Koharu\Test.khrproj` contains 7 JPEG source pages, probably the same images.

---

## 1. Which HTTP endpoints / MCP tools does Koharu expose?

### Current version (0.83.5): none

Evidence, all checked on this machine:

| Check | Result |
|---|---|
| `koharu.exe --help` | `Usage: koharu.exe`. The only options are `-h/--help` and `-V/--version`. |
| `koharu.exe --port 9999` | exit code 2: `error: unexpected argument '--port' found` |
| `koharu.exe --headless` | exit code 2: `error: unexpected argument '--headless' found` |
| Sockets owned by all 7 running `koharu.exe` processes (`Get-NetTCPConnection` / `Get-NetUDPEndpoint`) | **No listening TCP port, no UDP endpoint.** There is only one outbound HTTPS connection. |
| Source at the current HEAD: `crates/koharu/src/main.rs` | `struct Cli {}` has no fields. `crates/koharu-app/src/app.rs` registers Tauri IPC commands (`invoke_handler`). No axum, rmcp or `TcpListener` anywhere in the workspace. |
| CEF remote debugging (port 4000) | Enabled only in `#[cfg(debug_assertions)]` builds. Release builds don't have it. |
| Current docs (koharu.rs, `llms.txt` index) | No "Run GUI, Headless, and MCP Modes" or "MCP tools reference" pages anymore. Those URLs return 404. The docs moved to Mintlify in 0.81.8. |

**Why "headless crashes silently":** koharu.exe is a Windows-subsystem (no console) app. With `--headless` (or `--port`), clap rejects the flag and exits with code 2 before any window opens. The error message goes to a console that doesn't exist, so it looks like a silent crash. Nothing is wrong with your GPU or drivers. For the same reason, "GUI with `--port 9999`" cannot work on 0.83.5. The GUI that works is the one started with no arguments.

### History: when the API existed

From `CHANGELOG.md` and the tagged sources:

| Version | Date | Change |
|---|---|---|
| 0.24.0 | 2025-12-23 | headless mode ("to be used in browser") |
| 0.31.0 | 2026-02-08 | WebSocket-based RPC |
| 0.33.0 | 2026-02-14 | MCP server |
| 0.45.0 | 2026-04-19 | axum HTTP router (typed routes, SSE events, binary I/O) |
| **0.61.2** | 2026-06-16 | **last release with HTTP API + MCP** (`rmcp` and `koharu-rpc` still present) |
| 0.63.0 | 2026-08-11 | `koharu-rpc` / `rmcp` gone |
| 0.65.0 → 0.67.2 | 2026-08 | "Use CEF to replace Tauri", then "Use Tauri with CEF" |

For reference, the 0.61.2 surface (read from source, **not** tested, since that version isn't installed):

- **CLI:** `--port <PORT>`, `--host <HOST>` (default `127.0.0.1`), `--headless`, `--cpu`, `--debug`, plus a "download dynamic libraries and exit" flag. Without `--port`, it binds from 4000 upward until a port is free.
- **HTTP**, under `/api/v1`:
  - `GET /meta`, `/config`, `/engines`, `/events` (SSE), `/scene.json`, `/scene.bin`, `/blobs/{hash}`, `/projects`, `/operations`, `/llm/current`, `/llm/catalog`, `/pages/{id}/thumbnail`, `/fonts`, `/downloads`
  - `POST /projects`, `/pages`, `/pages/from-paths`, `/pipelines`, `/history/apply|undo|redo`, `/projects/current/export`, `/projects/import`
  - `PUT /llm/current`, `/config/providers/{id}/secret`, `/pages/{id}/masks/{role}`
  - `PATCH /config`
  - `DELETE /projects/current`, `/llm/current`, `/operations/{id}`
- **MCP**, at `/mcp`: tools `koharu.open_project`, `koharu.close_project`, `koharu.start_pipeline`, `koharu.apply`, `koharu.undo`, `koharu.redo`.

## 2. Can I send an image and get back boxes + Japanese + translation?

**Not through Koharu 0.83.5. There is no programmatic entry point.**

The data does exist inside Koharu after a GUI run. Your `Test.khrproj/state-a.khr` contains:

- detection regions and geometry
- OCR source text, e.g. `飛頭蛮の赤蛮奇と申します`
- translations, e.g. `Nice to meet you all. My name is Akamaki of the Hitodama clan.`

They're stored as components like `dev.koharu.geometry`, `dev.koharu.analysis.ocr`, `dev.koharu.text.source` and `dev.koharu.text.translation`.

The file is not readable directly, though:

- It is a custom binary format: a `KHRSTATE` header, a blake3 checksum, a body serialized with SurrealDB's `revision` crate, and a nested scene payload.
- It is undocumented and versioned.
- The docs warn not to touch project files while the project is open.

Reading it from Python would mean reverse-engineering, and it would break with Koharu updates. I don't recommend it.

## 3. Provider / model / Instructions: GUI or per request?

**In 0.83.5, GUI only.** There is no API, so nothing can be set per request.

- Settings are stored in `~/.koharu/config.toml` (non-secret) and in the Windows Credential Manager (API keys).
- Instructions are global. Per the docs: "apply to every project; there is no per-project field".
- **OpenRouter** is a built-in hosted provider (alongside OpenAI, Gemini, Claude, Grok, MiniMax, DeepSeek, LM Studio and OpenAI-compatible). It can be selected **only in the GUI** (Settings → providers). Editing `config.toml` by hand while Koharu is closed is technically possible, but the docs warn that invalid edits can stop it from starting.

Your current `config.toml`:

- detection `koharu-layout-rfdetr-seg-2xl`, OCR `paddleocr-vl-1.6`, inpainting `lama`
- translation: provider `local`, model `gemma4-26b-a4b-it`
- `generation.reasoning = false`, `vision = true`
- target `en-US`, with your custom scanlation Instructions

(In 0.61.2, `PATCH /api/v1/config`, `PUT /llm/current` and `PUT /config/providers/{id}/secret` would have allowed this through the API, but again: that's the old version.)

## 4. Authentication and bind address

- **0.83.5:** not applicable. Nothing listens, on 127.0.0.1 or any other interface.
- **0.61.2** (from source, if you ever downgrade):
  - **no authentication**
  - `CorsLayer::very_permissive()`
  - bound to `127.0.0.1` by default, or to whatever `--host` you pass
  - Any local process, or any web page you open in a browser (because CORS is fully open), could drive it. Never pass `--host 0.0.0.0` without a reverse proxy that adds auth.

## 5. Timing on the RTX 5080 Laptop (16 GB)

I couldn't measure this through an API. These numbers come from your own GUI runs in `%LOCALAPPDATA%\koharu\logs\koharu.log` (19:14 and 19:19–19:20 runs):

| Stage | Time | Source |
|---|---|---|
| Detection (rfdetr-seg-2xl) | not logged | the Torch models don't log timings |
| OCR model load (PaddleOCR-VL 1.6, 0.3B BF16) | 1–3 s | log timestamps |
| OCR per text block | ~20–110 ms image encode + ~10 ms decode (first block slowest) | `image slice encoded in … ms` |
| Translation model load (Gemma 4 26B-A4B, Q4_0 QAT GGUF, 31/31 layers on GPU) | 7–8 s | timestamps |
| **Vision: page image encode for Gemma** | **35.3 s and 36.0 s** | `image slice encoded in 35293 ms` |
| Translation text generation | not logged | — |

Observations:

- The **~35 s vision encode is abnormal.** It should take well under a second on this GPU. While Koharu is open, VRAM is at **15.8 / 16.3 GB**, so the vision projector probably runs with the driver spilling into shared system memory. Turning **Vision OFF** will very likely save ~35 s per page. Running Gemma at a smaller quantization, or the vision model alone, would also help.
- Koharu loads the OCR model *and* Gemma in the same run, so the "one large model at a time" rule is already tight inside Koharu itself.
- Between 19:12 and 19:19 the log shows five `pipeline ocr failed: failed to load paddleocr-vl-1.6` errors. This fits VRAM pressure.
- Ollama was not running during these tests. I did not stop anything.

---

## Proposed alternative pipeline (not implemented)

Simplest route: a small Python service that does what Koharu does internally, using the same kind of models, and returns szurubooru notes. Run it **with Koharu closed** so only one large model is in VRAM.

1. **Text/bubble detection**: `ogkalu/comic-text-and-bubble-detector` (RT-DETR, the same detector Koharu ships as `comic_text_bubble_detector`), loaded with `transformers`. Output: text/bubble boxes in pixels. Merge each text box into the bubble that contains it, and sort blocks in manga reading order (right-to-left, top-to-bottom).
2. **OCR**: run `manga-ocr` (`kha-white/manga-ocr-base`, about 450 MB) on each crop, plus a few pixels of padding. It is fast on GPU (tens of ms per crop) and good at vertical text.
3. **Translation**: **one** OpenAI-compatible `POST /v1/chat/completions` call per page.
   - Reuse Koharu's prompt shape:
     - user message = `{"source_language":"ja","target_language":"en","segments":[{"id":0,"text":"…"}, …]}`
     - `response_format` = JSON
     - expected reply = `{"translations":[{"id":0,"text":"…"}]}`
   - Put your current Instructions text in the system prompt.
   - Optionally attach the page image (vision). Measure whether it's worth the latency.
   - `base_url` / `model` / `api_key` come from per-request arguments or env vars. Switching between local (Ollama / LM Studio running Gemma 4) and **OpenRouter** is then a one-line change.
4. **Output**, per block:
   - `bbox_px = [x1, y1, x2, y2]`
   - `polygon = [[x1/W, y1/H], [x2/W, y1/H], [x2/W, y2/H], [x1/W, y2/H]]` (szurubooru note format)
   - `source`, `translation`

   This is the exact `translate_image(path)` contract you specified, so `koharu_client.py` could keep the same interface.

Dependencies: `torch` (CUDA build), `transformers`, `manga-ocr`, `Pillow`, `requests`. Install them in a **venv inside this folder**, not system-wide. Expected cost per page: detection well under 1 s, OCR about 0.5–1 s for ~15 blocks, plus one LLM call. With a local Gemma and vision off, that's a few seconds. With OpenRouter, the timing is network-bound.

Other options I considered, and why they rank lower:

- **Downgrade to Koharu 0.61.2** (last version with HTTP + MCP).
  - It would need a separate install, and it predates the models you use now (rfdetr layout, PaddleOCR-VL 1.6).
  - It reads the same `~/.koharu/config.toml`, so mixing versions risks breaking your current settings.
  - It's an abandoned API with no auth.
- **Parse `.khrproj` after GUI batch runs.** The data is there, but in an undocumented binary format (see §2). It's fragile, and it's not an endpoint.
- **Build Koharu's own Rust CLI tools** (`koharu-ml` bins such as `comic_text_bubble_detector`, `paddle_ocr_vl`, `manga_ocr`, and `koharu-translator`'s `translate` bin). This reuses Koharu's exact models and prompts, but needs a Rust + CUDA toolchain, which is a system-wide install. I'd ask you before doing it, and it's more work than the Python route.

## Not done / blocked

- `koharu_client.py` was not written, because Koharu has no API (your fallback instruction applies).
- Per-image `.json` outputs were not produced: `C:\Users\Enzof\translate-test\` does not exist, and there is no endpoint to call anyway.
- I didn't change Koharu settings, models, GPU, drivers or power settings. I didn't stop or restart the running Koharu instance, and I installed nothing. Docs and source were downloaded to the session scratchpad only.

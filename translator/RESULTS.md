# Test results: manga-translate service

## Current setup: RF-DETR 2XL (ONNX) + PaddleOCR-VL 1.6, CPU only (2026-11-08)

Machine: Ryzen 9 9955HX3D, Windows 11. Detection + OCR on CPU (`THREADS=4` unless noted). LLM = local Gemma 4
26B A4B (stand-in for OpenRouter). Outputs: `translate-test/<name>.json` + `_preview.png`; old outputs
(manga-ocr + ogkalu) in `bench/before_mangaocr/`. Raw numbers: `bench/run_cpu4_paddle.json`,
`bench/cpu_t4_4img.json`, `bench/cpu_t2_4img.json`.

### Timings and RAM

| | 4 threads | 2 threads |
|---|---|---|
| Detection per page | median 3.5 s (3.3–3.9) | median 5.5 s (max 5.7) |
| OCR per note | median 1.4 s (1.1–4.5) | median 2.8 s (max 7.8) |
| Total per page incl. LLM, 10 pages | median 12.3 s (6.7–26.4); LLM 3.1 s of it | — |
| Peak RAM, service / OCR server / **combined** | 3.1 / 1.9 / **4.9 GB** | same |
| Idle RAM (between requests) | 0.26 + 1.88 GB | same |

2-thread numbers are from the 4 iteration pages (7698, 9728, 28010, 12_dbae), the end-to-end
run used all 10. A VPS vCPU is usually slower: budget 1.5–2× these times.
RAM fix: ONNX Runtime memory arena off (6.9 → 3.1 GB peak, 0.26 GB idle) and mask head pruned
(identical detections). A pre-downscale of the page was tried and dropped: it changed scores by up to 0.8.

### Before / after per page (old: manga-ocr + ogkalu, new: PaddleOCR-VL + RF-DETR)

| Page | Notes old → new | Result |
|---|---|---|
| 7698 | 2 → 4 | Old merged 3 lines/2 speakers into garbage. New: 4 correct utterances; only こ、こころ still misread (マ、マニろ). |
| 9728 | 5 → 6 | 孕ませて now correct (old 学ませて). The long pale block was garbage, now correct text. |
| 28010 | 4 → 4 | Reading order now correct (ねーねー → きのうね → えーりんが → こんど). こんど/おはなばたけ fixed; えーいん on 1 of 3 えーりん. |
| 12_dbae | 5 → 4 | Same 4 dialogue lines, cleaner (ごんな for こんな). チンポでかっ lost: see below. |
| 21157 | 8 → 7 | Fewer dot artefacts; moans on bubble edges now merged into their bubble (see below). |
| 27620, 27651 | 3 → 2 each | Same text; lines of one bubble now form one note. |
| 9434, 27799 | 4 → 4, 1 → 1 | Same text, proper ellipses/♥ instead of `．．．`. |
| zwrazwrazw | 3 → 2 | Stray （３） gone; レンッッ (sound effect) now skipped. |

### Known issues

- **チンポでかっ (12_dbae)** is free-floating yellow lettering, not inside a bubble, and the model scores
  it as onomatopoeia (0.61), so it stays skipped. The "onomatopoeia inside a bubble = text" rule cannot help here.
- **Bubble rule side effect (21157):** breath marks (ふ～～, う～～) drawn on bubble edges count as ≥ 60% inside,
  so they are merged into the bubble's OCR text ("ふ・・・このことは", "3咲夜"). Translations were still right.
- **LLM omissions:** in the full run Gemma left 3 of 4 lines of 12_dbae untranslated once (`missing_ids`);
  3 reruns were complete. There is no automatic retry for missing ids yet.
- The preview font has no ❤ (U+2764) glyph, so it renders as a box (preview only, JSON is fine).

---

## Previous setup (manga-ocr + ogkalu), 2026-11-07

Date: 2026-11-07. Machine: RTX 5080 Laptop (16 GB), Ryzen 9 9955HX3D (16C/32T), Windows 11.
Koharu was closed during all tests (it was closed through its window at the start). Ollama was not running.

Outputs: `translate-test/<name>.json` + `translate-test/<name>_preview.png` (final GPU + Vision run).
Raw numbers: `bench/*.json`. Vision-off and CPU end-to-end JSONs: `bench/novision/`, `bench/cpu_e2e/`.

## Setup that was tested

| Stage | What | Where |
|---|---|---|
| Detection | `ogkalu/comic-text-and-bubble-detector` (RT-DETR v2, classes bubble / text_bubble / text_free; text classes used), threshold 0.35 | torch 2.11 cu128 or CPU |
| OCR | `kha-white/manga-ocr-base`, batched, crop without padding + autocontrast | torch 2.11 cu128 or CPU |
| Filter | blocks whose OCR has no kana/kanji are dropped (Latin text, digits, bare punctuation); returned in `meta.skipped` | — |
| LLM | Gemma 4 26B A4B **official** QAT GGUF (UD-Q4_K_XL) + mmproj-F16, the files Koharu had already downloaded, served by llama.cpp b12189 `llama-server` (portable, in `tools/`) | GPU, `--fit` auto-offload |
| LLM settings | Vision on, reasoning off (`reasoning_budget 0` + per-request flags), temperature 0.3, JSON mode, instructions.txt (your Koharu instructions) + glossary.txt | — |

## Timings per page (10 test pages, 1280×1429 to 4500×5500)

Median [min–max], in milliseconds. "Total" is server-side and excludes the HTTP upload.

### End to end through the HTTP API

| Run | Detection | OCR | LLM | **Total** |
|---|---|---|---|---|
| GPU detect/OCR, Vision **on** (final) | 56 [38–244] | 178 [102–454] | 2810 [2027–4458] | **3071 [2197–4976]** |
| GPU detect/OCR, Vision **off** | 58 [37–206] | 210 [97–491] | 1800 [734–3698] | **2092 [925–4252]** |
| **CPU detect/OCR (4 threads)**, Vision on | 472 [453–597] | 947 [445–2251] | 1818 [1171–3384] | **3100 [2371–6148]** |

The LLM column in the CPU run is still the local GPU llama-server. It is faster there only because its prompt cache was warm from the previous run.
On the VPS, the LLM time will be whatever OpenRouter's latency is (not measured, see below).

### Detection + OCR only, in-process (`bench.py`, after a warm-up image)

| Device | Detection | OCR | Det + OCR | OCR per box |
|---|---|---|---|---|
| GPU (RTX 5080) | 69 [45–234] | 226 [120–694] | **321** [171–774] | 58 |
| CPU 16 threads | 260 [235–400] | 711 [320–1787] | **1011** [579–2059] | 156 |
| CPU 8 threads | 307 [291–433] | 703 [350–1740] | **1068** [644–2062] | 173 |
| CPU 4 threads | 477 [337–628] | 1033 [496–1942] | **1589** [908–2459] | 255 |
| CPU 2 threads | 757 [472–877] | 1595 [887–3283] | **2417** [1380–4076] | 427 |

- Model load: ~2.5 s on CPU, a few s on GPU (first run downloads ~600 MB into `models/hf/`).
- RAM: about 1.5 GB for the service.
- VPS vCPUs are usually slower per core than this desktop Zen 5, so budget roughly 1.5–2× the 2/4-thread numbers.
- CPU and GPU produce the same results: 9/10 pages are identical, and the 10th differs only by one box edge being 1 px off.

### Tokens per request (llama-server returns `usage`; it is also in each JSON under `meta.llm.usage`)

| | Prompt tokens | Completion tokens |
|---|---|---|
| Vision on | 1230 [1075–1430] | 96 [36–248] |
| Vision off | 488 [332–695] | 96 [35–232] |

The page image costs about 550–750 prompt tokens. The system prompt (instructions + format) is about 300 tokens.
Generation runs at ~100 tokens/s on the local GPU.

## Quality and misses per image (from the previews)

38 Japanese blocks were translated, and 3 boxes were skipped as non-Japanese. No segment came back untranslated.

| Image | Blocks | What was missed or wrong |
|---|---|---|
| 12_dbae… | 5 | All dialogue found. SFX スリスリ / すー not detected. |
| 21157_9b91… | 8 | All dialogue found, including small ones. Hand-drawn SFX (びくっ, ふ〜, ドキッ, ほかっ…) not detected. |
| 27620_1978… | 3 | All dialogue found; translations clean. |
| 27651_d0fe… | 3 | All dialogue found; translations clean. |
| 27799_b23a… | 1 (+1 skipped) | Dialogue fine. The English SFX "Munch" was detected and correctly **skipped**; the second "Munch" was not detected (it would have been skipped anyway). |
| 28010_3f1b… (4500×5500) | 4 | All 4 bubbles found. **Reading order wrong** for 2 bubbles (left one sent before the right one), so the context is slightly off. Tiny background text on the blackboard not detected. |
| 7698_c715… | 2 | **OCR failure** on the big 5-column block (white outlined text on pink): the source is garbage. The LLM reconstructed part of the meaning but **invented names** ("Mori", "Nicola"). |
| 9434_20b7… | 4 (+2 skipped) | All dialogue found. 2 tiny SFX boxes OCR'd as symbols and correctly skipped. |
| 9728_022c… | 5 | **OCR failure** on the 6-column pale-green block (block 4): the source is garbage, and the LLM with Vision recovered most of it but invented "Rindou". In block 2, 孕ませて was misread as 学ませて, which became "teach you a lesson". SFX not detected. |
| zwrazwrazw | 3 | Both bubbles found. Large SFX (ず, しっ, ムワッ, ムンッ) not detected. |

Summary of the weak spots:

1. **Sound effects are mostly not detected** at threshold 0.35. Lowering it picks up some SFX, but also more junk.
2. **Long multi-column blocks with pale or outlined lettering** defeat manga-ocr (7698, 9728). Vision lets the LLM repair some of it, but it also hallucinates.
3. **Reading order** is a simple heuristic (rows, then right to left); it is wrong on 28010.

## Changes made during testing (and why)

- **OCR crops:** no padding + autocontrast. Padding pulled in neighbouring glyphs (屠自古 read as 扇/属自古, メス穴 as メスペ). Autocontrast fixed pale text.
- **Glossary:** it is now also put in the user JSON next to the segments. When it was only in the system prompt, Gemma ignored it ("Torajiko", "Kujon-tei"). Now: Tojiko, Geidontei, Youmu, Eirin are all correct.
- **llama-server:**
  - `-ub 2048` gives a larger image-token budget for Vision.
  - `--fit-target 2048` keeps 2 GB of VRAM headroom. With 1 GB, one request hit **WDDM paging** (prompt eval jumped from 1.4 to 18 ms/token: 17.7 s instead of 1.5 s). This is the same effect as Koharu's 35 s vision encode.
  - `--api-key` (from `LOCAL_LLM_API_KEY`): without it, llama-server accepts requests from any web page.
- **Client:** writes previews atomically and continues if a file is locked.

## Security checks done

- `/translate` without a key or with a wrong key → 401.
- `image_url` checks:
  - A loopback/private target without an allowlist → 403.
  - A `file://` URL → 400.
  - An allowlisted host → OK.
  - Redirects are not followed.
- The service binds to 127.0.0.1 only, and llama-server binds to 127.0.0.1 only and requires its key.

## Not tested

- **OpenRouter:** there is no OpenRouter key on this machine. The code sends the documented `reasoning: {"enabled": false}` and falls back to no `response_format` on HTTP 400. To test, put the three `LLM_*` lines from `.env.example` into `.env`, then run `python client.py translate-test\zwrazwrazw.jpeg`.
- **Linux:** the VPS install itself was not tested. The CPU path was tested on Windows with `DEVICE=cpu`.

## Ideas if quality needs to go further (not implemented)

- For blocks whose OCR looks wrong (long, many columns), also attach the block **crop** as an extra image so the LLM can read it directly.
- Use a stronger OCR for big blocks (PaddleOCR-VL 1.6, which Koharu uses). It is a second model, though.
- Better reading order: group blocks by panel (the detector's `bubble` boxes, or panel detection).
- Optionally include SFX: a lower threshold for `text_free`, flagged as `kind: "sfx"` so szurubooru can style them differently.

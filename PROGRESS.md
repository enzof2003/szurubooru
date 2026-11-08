# Progress (2026-11-08, pre-VPS fixes)

## Done this round
- **No orphan OCR server.** New `childproc.py`: Windows job object (kill-on-close), Linux
  `PR_SET_PDEATHSIG=SIGKILL` spawned from a keeper thread (PDEATHSIG fires when the forking *thread* exits,
  and the pipeline is built in a threadpool worker). Plus atexit, SIGTERM/SIGHUP/SIGBREAK → SystemExit when no
  handler is installed (uvicorn keeps its own), and a startup refusal if `OCR_PORT` is already taken.
  Verified: Windows `taskkill /F` on the service's python (and on the venv launcher) → llama-server gone;
  Linux (WSL, dummy child): child survives its spawning thread exiting, dies on `kill -9` of the parent.
  Not yet verified on the VPS itself.
- **Onomatopoeia-in-bubble threshold 60% → 90%** (`ONOMATOPOEIA_IN_BUBBLE`; text grouping stays at 60%).
  21157: the breath marks at 57–75% are skipped again (sources/translations cleaner, e.g. "…あ…あ……ありがと");
  く～～ inside a bubble is still promoted. 12_dbae unchanged (4 blocks).
- **Translation completeness.** Segments missing/empty/null in the LLM reply get one retry with only those
  segments (original ids). Still missing → `translation: null` + `meta.untranslated` (never `""`);
  `meta.llm` has `retried_ids`, `missing_ids`, `retry_error`. Unit-tested with a fake LLM (recover / partial /
  retry HTTP error / complete). Live: 21157 + 12_dbae complete on the first call (no retry triggered).

## Open
- チンポでかっ (12_dbae) remains skipped as onomatopoeia (not in a bubble).
- Preview font lacks ❤ (U+2764).
- OpenRouter not yet tested end to end.

---

Previous round:

# Progress (2026-11-08): PaddleOCR-VL + RF-DETR switch is complete

All items from the previous PROGRESS.md are done. Details and numbers: `RESULTS.md` (top section), `README.md`.

## Done this round
- RAM fix applied in `pipeline.RfDetr`: `enable_cpu_mem_arena = False`; default `DET_MODEL` is the
  mask-pruned `rfdetr-seg-2xlarge-nomask.onnx` (bit-identical detections on the 4 test pages);
  `tools/export_rfdetr_onnx.py` now also writes the pruned model. The planned pre-downscale was
  **not** kept: it shifted scores by up to 0.8 for ~90 MB of savings.
- Peak RAM: service 3.1 GB + OCR server 1.9 GB = **4.9 GB combined** (was 6.9 + 1.9). Idle 0.26 + 1.9 GB.
  VPS has ~8 GB free (9 GB total, no swap) → 2XL kept, ~3 GB headroom. `bench.py` now reports
  `peak_combined_mb` and `idle_rss_mb`.
- New rule in `build_notes`: onomatopoeia ≥ 60% inside a bubble → text (`origin: onomatopoeia_in_bubble`
  in `meta.notes`). Fires on 21157 (4 bubbles). Does not fire on 12_dbae: チンポでかっ is not in a bubble.
- Full end-to-end run (THREADS=4, local Gemma) regenerated all `translate-test/*.json` + previews
  (`bench/run_cpu4_paddle.json`); 12_dbae re-run once after an LLM omission.
- README rewritten (VPS install without torch, Linux llama-server, `.env` keys, RAM); RESULTS has a short
  summary + per-page before/after on top, old report kept below.

## Open / possible next steps (not started)
- Retry the LLM call for `missing_ids` (Gemma dropped 3 of 4 lines once on 12_dbae).
- Bubble rule picks up breath marks on bubble edges (21157); could require e.g. ≥ 90% inside.
- チンポでかっ (12_dbae) remains skipped as onomatopoeia.
- Preview font lacks ❤ (U+2764).
- OpenRouter not yet tested end to end.
- If the service is killed hard on Windows, its OCR `llama-server` child keeps running.

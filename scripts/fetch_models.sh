#!/usr/bin/env bash
# Downloads PaddleOCR-VL 1.6 (GGUF + vision projector, ~1.8 GB) from Hugging Face into models/paddleocr-vl/.
# Pinned to a repo revision and checked against SHA-256; re-running skips verified files and resumes partial ones.
# Optional: HF_TOKEN=hf_... for authenticated (higher rate limit) downloads.
#
# The detector (models/rfdetr/rfdetr-seg-2xlarge-nomask.onnx, 144 MB) is NOT on Hugging Face as ONNX:
# copy it from the dev machine, or rebuild it with tools/export_rfdetr_onnx.py (needs requirements-dev.txt).
set -euo pipefail

REPO="PaddlePaddle/PaddleOCR-VL-1.6-GGUF"
REV="511b09642bb324401f15f97cc23bc67e8f0a291d"
DEST="$(cd "$(dirname "$0")/.." && pwd)/models/paddleocr-vl"

FILES=(
  "PaddleOCR-VL-1.6-GGUF.gguf f3ae46ec885050acf4b3d31944431e1fd90d50664fb09126af4a3c050ba14ee8"
  "PaddleOCR-VL-1.6-GGUF-mmproj.gguf 204d757d7610d9b3faab10d506d69e5b244e32bf765e2bab2d0167e65e0a058a"
)

auth=()
if [[ -n "${HF_TOKEN:-}" ]]; then auth=(-H "Authorization: Bearer ${HF_TOKEN}"); fi

mkdir -p "$DEST"
for entry in "${FILES[@]}"; do
  name="${entry%% *}"; sha="${entry##* }"
  out="$DEST/$name"
  if [[ -f "$out" ]] && echo "$sha  $out" | sha256sum -c --status; then
    echo "ok       $name (already present)"
    continue
  fi
  echo "download $name"
  curl -fL --retry 5 --retry-delay 5 -C - "${auth[@]}" \
    -o "$out.part" "https://huggingface.co/$REPO/resolve/$REV/$name"
  if ! echo "$sha  $out.part" | sha256sum -c --status; then
    echo "SHA-256 mismatch for $name; removing the partial file, run again" >&2
    rm -f "$out.part"
    exit 1
  fi
  mv "$out.part" "$out"
  echo "ok       $name"
done
echo "models in $DEST"

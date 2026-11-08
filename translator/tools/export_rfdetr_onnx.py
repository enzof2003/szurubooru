"""One-off: export Koharu's RF-DETR Seg 2XL (models/rfdetr/model.safetensors) to ONNX.

Needs the dev extras (requirements-dev.txt: torch, rfdetr==1.7.0, safetensors, onnx).
The service itself only needs onnxruntime + the resulting models/rfdetr/rfdetr-seg-2xlarge-nomask.onnx
(the full export with the mask head pruned: the pipeline reads only boxes + logits, and the
mask branch costs RAM and time).

    .venv\\Scripts\\python tools\\export_rfdetr_onnx.py
"""
import hashlib

import onnx.utils
from pathlib import Path

from rfdetr import RFDETRSeg2XLarge
from safetensors.torch import load_file

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = ROOT / "models" / "rfdetr" / "model.safetensors"
EXPECTED_SHA256 = "9bf6d2cbd7793c956d8c857bb1672a396eb7f100eb0682f86830d05e31168efb"

digest = hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()
if digest != EXPECTED_SHA256:
    raise SystemExit(f"unexpected checksum {digest}")

model = RFDETRSeg2XLarge(pretrain_weights=None, num_classes=4, resolution=1152, device="cpu")
model.model.model.load_state_dict(load_file(WEIGHTS))  # strict: fails on any mismatch
model.model.model.eval()
out = model.export(output_dir=str(WEIGHTS.parent), simplify=False, verbose=False)
print("exported", out)

full = WEIGHTS.parent / "rfdetr-seg-2xlarge.onnx"
pruned = WEIGHTS.parent / "rfdetr-seg-2xlarge-nomask.onnx"
inp = onnx.load(str(full), load_external_data=False).graph.input[0].name
onnx.utils.extract_model(str(full), str(pruned), input_names=[inp], output_names=["dets", "labels"])
print("pruned", pruned)

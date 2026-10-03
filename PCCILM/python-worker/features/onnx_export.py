"""One-off tool: export the MobileCLIP-S0 *image* encoder to ONNX, so the shipped app needs no torch.

Developer machine only (imports torch and mobileclip). The runtime counterpart is
:mod:`features.onnx_encoder`, which needs only onnxruntime, numpy and pillow.

The exported graph takes the preprocessed image ``float32 [1, 3, 256, 256]`` (values 0..1, as MobileCLIP's
own transform produces) and returns the **L2-normalized** 512-d embedding, so it matches
``MobileClipImageEncoder.embed_frame`` exactly in what it returns. The text tower is not exported.

    python -m features.onnx_export --checkpoint ..\\Member1\\checkpoints\\mobileclip_s0.pt --out mobileclip_s0_image.onnx

Always run ``python -m eval.verify_onnx_encoder`` afterwards: stored prototypes and embeddings were made
with torch, so the ONNX embeddings must stay close to them.
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

from features.mobileclip_encoder import EMBED_DIM, INPUT_SIZE, MODEL_NAME, _resolve_checkpoint

OPSET = 17


def export(checkpoint: str | Path | None, out: str | Path, *, opset: int = OPSET) -> Path:
    import torch

    resolved = _resolve_checkpoint(checkpoint)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        import mobileclip

        model, _, _ = mobileclip.create_model_and_transforms(MODEL_NAME, pretrained=str(resolved))
    del model.text_encoder  # image tower only
    model = model.eval()
    for param in model.parameters():
        param.requires_grad_(False)

    class ImageEncoder(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, pixels):
            features = self.inner.encode_image(pixels)
            return features / features.norm(dim=-1, keepdim=True)

    wrapper = ImageEncoder(model).eval()
    example = torch.zeros(1, 3, INPUT_SIZE, INPUT_SIZE, dtype=torch.float32)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with torch.inference_mode(), warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        torch.onnx.export(
            wrapper, example, str(target),
            input_names=["pixels"], output_names=["embedding"],
            opset_version=opset, dynamo=False,
        )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", default=None, help="mobileclip_s0.pt (or set MOBILECLIP_S0_CHECKPOINT)")
    parser.add_argument("--out", default="mobileclip_s0_image.onnx")
    args = parser.parse_args()
    path = export(args.checkpoint, args.out)
    print(f"wrote {path} ({path.stat().st_size / 1024 / 1024:.1f} MB), output {EMBED_DIM}-d L2-normalized")


if __name__ == "__main__":
    main()

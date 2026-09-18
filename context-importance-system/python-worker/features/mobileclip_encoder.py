"""MobileCLIP-S0 image embeddings.

Loads the MobileCLIP-S0 **image encoder only** (frozen weights, text tower dropped) and turns a
screen frame into a 512-d L2-normalized vector via :func:`embed_frame`.

MobileCLIP is not on PyPI. Install Apple's ``ml-mobileclip`` package and place the S0 checkpoint
(``mobileclip_s0.pt``) where ``load_image_encoder`` can find it (``--checkpoint`` in the tools, or
the ``MOBILECLIP_S0_CHECKPOINT`` environment variable). ``requirements.txt`` pins torch/pillow/numpy;
mobileclip itself is installed separately.

The frozen, text-free encoder is the only model this component owns. Similarity/retrieval against
text is explicitly out of scope for this member (see ../../CONTRIBUTING.md).
"""

from __future__ import annotations

import gc
import os
import threading
import warnings
from pathlib import Path
from typing import Union

import numpy as np

EMBED_DIM = 512
MODEL_NAME = "mobileclip_s0"
INPUT_SIZE = 256  # MobileCLIP-S0 preprocessing resizes the short side to 256 and centre-crops.

# Accepts a PIL image or a raw frame array. A 4-channel array is treated as the collector's BGRA
# frame; a 3-channel array is treated as RGB.
ImageLike = Union["Image.Image", np.ndarray]

_ENV_CHECKPOINT = "MOBILECLIP_S0_CHECKPOINT"

_default_encoder: "MobileClipImageEncoder | None" = None
_default_lock = threading.Lock()


class MobileClipImageEncoder:
    """A loaded, frozen MobileCLIP-S0 image encoder. Not tied to any global state."""

    def __init__(self, model, preprocess, device) -> None:
        self._model = model
        self._preprocess = preprocess
        self._device = device

    @property
    def device(self):
        return self._device

    def embed_frame(self, image: ImageLike) -> np.ndarray:
        """Return a 512-d float32 unit vector for ``image`` (a PIL image or a frame array)."""
        import torch

        pil = _to_pil(image)
        tensor = self._preprocess(pil).unsqueeze(0).to(self._device)
        with torch.inference_mode():
            features = self._model.encode_image(tensor)
            features = features / features.norm(dim=-1, keepdim=True)
        vector = features[0].detach().to("cpu", dtype=torch.float32).numpy()
        return np.ascontiguousarray(vector)


def load_image_encoder(
    checkpoint: str | os.PathLike | None = None,
    device: str = "cpu",
    *,
    drop_text_encoder: bool = True,
) -> MobileClipImageEncoder:
    """
    Load MobileCLIP-S0 and return its image encoder.

    The full checkpoint (which contains both towers) is read once; when ``drop_text_encoder`` is
    set, the text tower is deleted and garbage-collected straight away, so it is not resident during
    inference and does not count toward the encoder's memory footprint.
    """
    # Resolve first: importing torch can take a long time, and a missing checkpoint should fail fast.
    resolved = _resolve_checkpoint(checkpoint)
    import torch

    with warnings.catch_warnings():
        # mobileclip pulls in deprecated timm.models.layers; silence its FutureWarning.
        warnings.filterwarnings("ignore", category=FutureWarning)
        import mobileclip

        model, _, preprocess = mobileclip.create_model_and_transforms(
            MODEL_NAME, pretrained=str(resolved)
        )

    if drop_text_encoder and hasattr(model, "text_encoder"):
        del model.text_encoder
        gc.collect()

    torch_device = torch.device(device)
    model = model.to(torch_device).eval()
    for param in model.parameters():
        param.requires_grad_(False)  # frozen: only ever run, never trained

    return MobileClipImageEncoder(model, preprocess, torch_device)


def default_encoder() -> MobileClipImageEncoder:
    """The process-wide default encoder (CPU, checkpoint from env), loaded on first call.

    Raises if it cannot be loaded (missing checkpoint, torch/mobileclip not installed); a later call
    retries, so callers that embed every frame should stop calling after a failure.
    """
    global _default_encoder
    if _default_encoder is None:
        with _default_lock:
            if _default_encoder is None:
                _default_encoder = load_image_encoder()
    return _default_encoder


def embed_frame(image: ImageLike) -> np.ndarray:
    """Embed ``image`` with the lazily-loaded, process-wide default encoder."""
    return default_encoder().embed_frame(image)


def _resolve_checkpoint(checkpoint: str | os.PathLike | None) -> Path:
    candidate = checkpoint or os.environ.get(_ENV_CHECKPOINT)
    if not candidate:
        raise FileNotFoundError(
            "MobileCLIP-S0 checkpoint not given. Pass checkpoint=..., or set "
            f"{_ENV_CHECKPOINT} to the path of mobileclip_s0.pt."
        )
    path = Path(candidate)
    if not path.is_file():
        raise FileNotFoundError(f"MobileCLIP-S0 checkpoint not found: {path}")
    return path


def _to_pil(image: ImageLike):
    from PIL import Image

    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if isinstance(image, np.ndarray):
        if image.dtype != np.uint8:
            raise TypeError(f"frame array must be uint8, got {image.dtype}")
        if image.ndim == 3 and image.shape[2] == 4:
            # Collector frames are BGRA; take BGR->RGB and drop alpha.
            return Image.fromarray(image[:, :, [2, 1, 0]], mode="RGB")
        if image.ndim == 3 and image.shape[2] == 3:
            return Image.fromarray(image, mode="RGB")
        raise ValueError(f"unsupported frame array shape {image.shape}")
    raise TypeError(f"embed_frame expects a PIL image or numpy array, got {type(image).__name__}")

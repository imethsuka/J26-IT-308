"""MobileCLIP-S0 image embeddings through ONNX Runtime: no torch, no mobileclip.

Drop-in for :class:`features.mobileclip_encoder.MobileClipImageEncoder` (same ``embed_frame``, same
512-d L2-normalized float32 result), made for the shipped app: a few hundred MB of RAM instead of
~1.25 GB, and an installer without torch.

Preprocessing reproduces MobileCLIP's own transform with pillow and numpy only: resize the short side to
256 (bilinear, the long side truncated like torchvision does), centre-crop 256x256, scale to 0..1, CHW.
It is checked against the torch encoder by ``eval/verify_onnx_encoder.py``.

Resource limits are deliberate: the worker must not hog the PC, so inference is capped to a few threads
and the CPU memory arena is off (it only caches buffers the next frame would need anyway).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np

from app_paths import install_root

from .mobileclip_encoder import EMBED_DIM, INPUT_SIZE, ImageLike, _to_pil

ONNX_FILE_NAME = "mobileclip_s0_image.onnx"
_ENV_ONNX = "MOBILECLIP_ONNX_MODEL"
DEFAULT_THREADS = 2


class OnnxImageEncoder:
    """A loaded MobileCLIP-S0 image encoder running on ONNX Runtime (CPU)."""

    def __init__(self, model_path: str | os.PathLike, *, threads: int = DEFAULT_THREADS) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, threads)
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name
        self._lock = threading.Lock()  # one session, one frame at a time: keeps peak memory flat
        self.path = Path(model_path)

    @property
    def device(self) -> str:
        return "cpu (onnxruntime)"

    def embed_frame(self, image: ImageLike) -> np.ndarray:
        """Return a 512-d float32 unit vector for ``image`` (a PIL image or a frame array)."""
        pixels = preprocess(image)
        with self._lock:
            (embedding,) = self._session.run(None, {self._input: pixels})
        vector = np.ascontiguousarray(embedding[0], dtype=np.float32)
        if vector.shape != (EMBED_DIM,):
            raise RuntimeError(f"ONNX encoder returned shape {vector.shape}, expected ({EMBED_DIM},)")
        # The graph already normalizes; re-normalize in float32 so tiny export differences never leak out.
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else vector


def preprocess(image: ImageLike) -> np.ndarray:
    """``float32 [1, 3, 256, 256]``, equal to MobileCLIP's ``Resize -> CenterCrop -> ToTensor``."""
    from PIL import Image

    pil = _to_pil(image)
    width, height = pil.size
    if width <= height:
        new_width, new_height = INPUT_SIZE, int(INPUT_SIZE * height / width)
    else:
        new_width, new_height = int(INPUT_SIZE * width / height), INPUT_SIZE
    if (new_width, new_height) != (width, height):
        pil = pil.resize((new_width, new_height), Image.BILINEAR)
    left = int(round((new_width - INPUT_SIZE) / 2.0))
    top = int(round((new_height - INPUT_SIZE) / 2.0))
    pil = pil.crop((left, top, left + INPUT_SIZE, top + INPUT_SIZE))
    array = np.asarray(pil, dtype=np.uint8)
    return np.ascontiguousarray(array.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0


def find_onnx_model(explicit: str | os.PathLike | None = None) -> Path | None:
    """The ONNX model to use: ``explicit``, ``MOBILECLIP_ONNX_MODEL``, or next to the app / worker."""
    candidates = [explicit, os.environ.get(_ENV_ONNX)]
    here = Path(__file__).resolve().parent.parent
    candidates += [here / "models" / ONNX_FILE_NAME, here / ONNX_FILE_NAME]
    if (root := install_root()) is not None:  # installed app: <install>\models\mobileclip_s0_image.onnx
        candidates.append(root / "models" / ONNX_FILE_NAME)
    candidates.append(Path(os.environ.get("LOCALAPPDATA", ".")) / "context-importance" / ONNX_FILE_NAME)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


_default: OnnxImageEncoder | None = None
_default_lock = threading.Lock()


def default_onnx_encoder() -> OnnxImageEncoder:
    """The process-wide ONNX encoder, loaded on first call. Raises ``FileNotFoundError`` if no model is found."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                path = find_onnx_model()
                if path is None:
                    raise FileNotFoundError(
                        f"{ONNX_FILE_NAME} not found. Set {_ENV_ONNX}, or export it with "
                        "`python -m features.onnx_export`."
                    )
                _default = OnnxImageEncoder(path)
    return _default

"""Tests for the encoder's input handling and the resource probe that do NOT require torch,
mobileclip, or the checkpoint (so they run in CI without the ~216 MB weights or the ML stack).

The end-to-end latency/memory numbers are produced by ``eval/measure_encoder.py``, which does need
the model; those are reported, not asserted.
"""

from __future__ import annotations

import numpy as np
import pytest

from features.mobileclip_encoder import EMBED_DIM, _to_pil
from eval.resource_probe import current_rss_bytes, peak_rss_bytes


def test_embed_dim_is_512() -> None:
    assert EMBED_DIM == 512


def test_bgra_frame_is_converted_rgb_dropping_alpha() -> None:
    frame = np.zeros((2, 2, 4), dtype=np.uint8)
    frame[..., 0] = 10   # B
    frame[..., 1] = 20   # G
    frame[..., 2] = 30   # R
    frame[..., 3] = 255  # A

    pil = _to_pil(frame)

    assert pil.mode == "RGB"
    assert pil.size == (2, 2)
    assert pil.getpixel((0, 0)) == (30, 20, 10)  # R, G, B


def test_rgb_frame_is_passed_through() -> None:
    frame = np.dstack([
        np.full((3, 3), 200, np.uint8),
        np.full((3, 3), 100, np.uint8),
        np.full((3, 3), 50, np.uint8),
    ])
    assert _to_pil(frame).getpixel((0, 0)) == (200, 100, 50)


def test_non_uint8_array_is_rejected() -> None:
    with pytest.raises(TypeError, match="uint8"):
        _to_pil(np.zeros((2, 2, 3), dtype=np.float32))


def test_wrong_channel_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="shape"):
        _to_pil(np.zeros((2, 2, 2), dtype=np.uint8))


def test_unsupported_type_is_rejected() -> None:
    with pytest.raises(TypeError):
        _to_pil("not an image")


def test_resource_probe_reports_positive_and_monotonic_peak() -> None:
    current = current_rss_bytes()
    peak = peak_rss_bytes()
    assert current > 0
    assert peak >= current

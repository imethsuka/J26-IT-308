"""Builders for a mock ``context_snapshot`` message, matching what the C# collector sends."""

from __future__ import annotations

from typing import Any


def mock_header(seq: int = 1, *, width: int = 4, height: int = 2, excluded: bool = False) -> dict[str, Any]:
    stride = width * 4
    context: dict[str, Any] = (
        {"excluded": True, "app": None, "title": None, "domain": None, "dwell_seconds": 0.0}
        if excluded
        else {
            "excluded": False,
            "app": "chrome",
            "title": "Docs - Example",
            "domain": "docs.example.com",
            "dwell_seconds": 12.5,
        }
    )
    return {
        "schema": "context_snapshot",
        "version": 1,
        "seq": seq,
        "ts": "2026-09-24T07:02:35.796737Z",
        "trigger": "frame",
        "context": context,
        "activity": {
            "window_seconds": 10,
            "key_presses": 42,
            "mouse_clicks": 3,
            "mouse_scrolls": 7,
            "mouse_moves": 310,
        },
        "idle": {"is_idle": False, "idle_seconds": 0.0},
        "frame": {
            "width": width,
            "height": height,
            "stride": stride,
            "format": "bgra32",
            "captured_at": "2026-09-24T07:02:35.796737Z",
            "diff": 6.98,
        },
    }


def mock_payload(width: int = 4, height: int = 2) -> bytes:
    # Deterministic BGRA bytes so tests can assert the payload survived the round trip.
    return bytes((i * 7) % 256 for i in range(width * height * 4))

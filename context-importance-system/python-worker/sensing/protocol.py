"""Wire protocol for the collector -> worker IPC channel.

This is the Python-side mirror of the C# ``CsharpCollector.Ipc`` types. Keep this file, the C#
``WireFormat`` / ``SnapshotHeader`` classes, and the README protocol section in sync.

Framing (all integers little-endian)::

    b"CIS1" (4 bytes) | header_length: u32 | payload_length: u32 | header: UTF-8 JSON | payload: raw frame bytes

The header is a JSON ``context_snapshot`` object. The payload, when present, is the raw frame
pixels described by ``header["frame"]`` (no compression, no base64). Sending the pixels beside the
JSON rather than base64-encoded inside it avoids a ~33% size increase and an encode/decode pass on
every ~8 MB frame.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, BinaryIO

MAGIC = b"CIS1"
PREFIX_FORMAT = "<4sII"  # magic, header_length, payload_length
PREFIX_SIZE = struct.calcsize(PREFIX_FORMAT)

SCHEMA_NAME = "context_snapshot"
SCHEMA_VERSION = 2
# Version 1 had no "text" object; the worker accepts both so an older collector keeps working.
SUPPORTED_VERSIONS = frozenset({1, 2})

# Guards against a desynced or hostile sender. A 4K BGRA frame is ~33 MB; this leaves headroom.
# The header carries the visible screen text (collector cap: 50,000 characters, up to ~4 bytes each
# once UTF-8 and JSON-escaped), so it is no longer tiny.
MAX_HEADER_BYTES = 1024 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024


class ProtocolError(Exception):
    """Raised when bytes on the wire do not match the framing or schema."""


@dataclass(frozen=True)
class FrameInfo:
    width: int
    height: int
    stride: int
    format: str
    captured_at: datetime
    diff: float | None

    @property
    def expected_payload_bytes(self) -> int:
        return self.stride * self.height


@dataclass(frozen=True)
class ScreenImage:
    """A large picture on screen: bounds in screen pixels and its alt text, if any."""

    x: int
    y: int
    width: int
    height: int
    alt: str | None


@dataclass(frozen=True)
class ScreenText:
    """The visible text and large images of the foreground window when the frame was captured."""

    source: str
    chars: int
    truncated: bool
    read_ms: float
    content: str = field(repr=False)  # raw screen text: never log it
    images: tuple[ScreenImage, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    """A decoded ``context_snapshot`` header plus the raw frame bytes that followed it."""

    seq: int
    ts: datetime
    trigger: str
    context: dict[str, Any]
    activity: dict[str, Any] | None
    idle: dict[str, Any]
    frame: FrameInfo | None
    payload: bytes = field(repr=False, default=b"")
    text: ScreenText | None = None

    @property
    def app(self) -> str | None:
        return self.context.get("app")

    @property
    def excluded(self) -> bool:
        return bool(self.context.get("excluded"))


def _parse_ts(value: str) -> datetime:
    # C# sends e.g. "2026-09-24T07:02:35.796737Z". fromisoformat handles this once Z -> +00:00.
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_header(header: dict[str, Any]) -> Snapshot:
    """Validate and decode a header. Any malformed field raises ``ProtocolError``."""
    if not isinstance(header, dict):
        raise ProtocolError(f"header must be a JSON object, got {type(header).__name__}")
    try:
        return _parse_header(header)
    except ProtocolError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ProtocolError(f"malformed header: {type(exc).__name__}: {exc}") from exc


def _parse_header(header: dict[str, Any]) -> Snapshot:
    schema = header.get("schema")
    version = header.get("version")
    if schema != SCHEMA_NAME:
        raise ProtocolError(f"unexpected schema {schema!r}")
    if version not in SUPPORTED_VERSIONS:
        raise ProtocolError(f"unsupported schema version {version!r}")

    frame_obj = header.get("frame")
    frame = None
    if frame_obj is not None:
        frame = FrameInfo(
            width=frame_obj["width"],
            height=frame_obj["height"],
            stride=frame_obj["stride"],
            format=frame_obj["format"],
            captured_at=_parse_ts(frame_obj["captured_at"]),
            diff=frame_obj.get("diff"),
        )

    return Snapshot(
        text=_parse_text(header.get("text")),
        seq=header["seq"],
        ts=_parse_ts(header["ts"]),
        trigger=header.get("trigger", ""),
        context=header.get("context") or {},
        activity=header.get("activity"),
        idle=header.get("idle") or {},
        frame=frame,
    )


def _parse_text(obj: Any) -> ScreenText | None:
    if obj is None:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("content"), str):
        raise ProtocolError("text must be an object with a string 'content'")
    images = tuple(
        ScreenImage(int(i["x"]), int(i["y"]), int(i["width"]), int(i["height"]), i.get("alt"))
        for i in obj.get("images") or ()
    )
    return ScreenText(
        source=str(obj.get("source", "")),
        chars=len(obj["content"]),
        truncated=bool(obj.get("truncated", False)),
        read_ms=float(obj.get("read_ms", 0.0)),
        content=obj["content"],
        images=images,
    )


def read_exactly(stream: BinaryIO, count: int) -> bytes:
    """Read exactly ``count`` bytes or raise; returns b\"\" only when count is 0."""
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("stream closed mid-message")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)

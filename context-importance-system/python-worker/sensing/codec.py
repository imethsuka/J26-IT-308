"""Encode and decode framed IPC messages.

``read_message`` is the receive path used by the listener. ``encode_message`` is the mirror send
path, used by tests (and any future Python-side sender) to produce bytes byte-identical to the C#
``WireFormat``.
"""

from __future__ import annotations

from dataclasses import replace
import json
import struct
from typing import Any, BinaryIO

from .protocol import (
    MAGIC,
    MAX_HEADER_BYTES,
    MAX_PAYLOAD_BYTES,
    PREFIX_FORMAT,
    PREFIX_SIZE,
    ProtocolError,
    Snapshot,
    parse_header,
    read_exactly,
)


def read_message(stream: BinaryIO) -> Snapshot:
    """Read one framed message from ``stream``. Raises ``EOFError`` at a clean end of stream."""
    prefix = read_exactly(stream, PREFIX_SIZE)
    magic, header_len, payload_len = struct.unpack(PREFIX_FORMAT, prefix)
    if magic != MAGIC:
        raise ProtocolError(f"bad magic {magic!r}")
    if header_len == 0 or header_len > MAX_HEADER_BYTES:
        raise ProtocolError(f"header length {header_len} out of range")
    if payload_len > MAX_PAYLOAD_BYTES:
        raise ProtocolError(f"payload length {payload_len} out of range")

    header_bytes = read_exactly(stream, header_len)
    try:
        header = json.loads(header_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"header is not valid UTF-8 JSON: {exc}") from exc

    payload = read_exactly(stream, payload_len) if payload_len else b""
    snapshot = parse_header(header)

    if snapshot.frame is not None and payload_len not in (0, snapshot.frame.expected_payload_bytes):
        raise ProtocolError(
            f"payload length {payload_len} does not match frame "
            f"{snapshot.frame.width}x{snapshot.frame.height} stride {snapshot.frame.stride}"
        )

    # dataclass is frozen; attach the payload via object.__setattr__ equivalent by rebuilding.
    return _with_payload(snapshot, payload)


def encode_message(header: dict[str, Any], payload: bytes = b"") -> bytes:
    """Serialize a header dict + payload into framed wire bytes."""
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    prefix = struct.pack(PREFIX_FORMAT, MAGIC, len(header_bytes), len(payload))
    return prefix + header_bytes + payload


def _with_payload(snapshot: Snapshot, payload: bytes) -> Snapshot:
    # replace() keeps every other field (including text), so a new Snapshot field can't be dropped here.
    return replace(snapshot, payload=payload)

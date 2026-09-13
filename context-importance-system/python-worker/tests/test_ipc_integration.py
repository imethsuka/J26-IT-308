"""End-to-end IPC tests: a mock collector sends framed messages over a real loopback socket to the
listener, and we assert the decoded snapshot and payload arrive intact.

The named-pipe transport is exercised separately in test_named_pipe.py (Windows only). The framing
is identical, so TCP here covers the codec + listener path on every platform.
"""

from __future__ import annotations

import socket
import threading

import pytest

from sensing.codec import encode_message, read_message
from sensing.protocol import ProtocolError, Snapshot
from tests.mock_snapshot import mock_header, mock_payload


def _connect(port: int) -> socket.socket:
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    client.settimeout(5)
    return client


def test_mock_message_end_to_end() -> None:
    received: list[Snapshot] = []
    done = threading.Event()

    def handler(snapshot: Snapshot) -> None:
        received.append(snapshot)
        done.set()

    from sensing.listener import TcpSnapshotListener

    listener = TcpSnapshotListener(port=0, handler=handler)
    listener.start_background()
    try:
        payload = mock_payload()
        client = _connect(listener.port)
        client.sendall(encode_message(mock_header(seq=7), payload))
        assert done.wait(timeout=5), "listener did not receive the message"
        client.close()
    finally:
        listener.shutdown()

    assert len(received) == 1
    snap = received[0]
    assert snap.seq == 7
    assert snap.trigger == "frame"
    assert snap.app == "chrome"
    assert snap.context["domain"] == "docs.example.com"
    assert snap.context["dwell_seconds"] == 12.5
    assert snap.activity["key_presses"] == 42
    assert snap.idle["is_idle"] is False
    assert snap.frame is not None
    assert (snap.frame.width, snap.frame.height) == (4, 2)
    assert snap.frame.diff == 6.98
    assert snap.payload == payload


def test_multiple_messages_over_one_connection() -> None:
    received: list[Snapshot] = []
    latch = threading.Event()

    def handler(snapshot: Snapshot) -> None:
        received.append(snapshot)
        if len(received) == 3:
            latch.set()

    from sensing.listener import TcpSnapshotListener

    listener = TcpSnapshotListener(port=0, handler=handler)
    listener.start_background()
    try:
        client = _connect(listener.port)
        for seq in (1, 2, 3):
            client.sendall(encode_message(mock_header(seq=seq), mock_payload()))
        assert latch.wait(timeout=5)
        client.close()
    finally:
        listener.shutdown()

    assert [s.seq for s in received] == [1, 2, 3]


def test_excluded_snapshot_has_no_app_and_may_omit_payload() -> None:
    header = mock_header(excluded=True)
    header["frame"] = None  # excluded contexts still capture, but a metadata-only message is valid too
    data = encode_message(header, b"")

    snap = read_message(_BytesStream(data))

    assert snap.excluded is True
    assert snap.app is None
    assert snap.frame is None
    assert snap.payload == b""


def test_bad_magic_is_rejected() -> None:
    data = encode_message(mock_header(), mock_payload())
    corrupted = b"XXXX" + data[4:]
    with pytest.raises(ProtocolError, match="magic"):
        read_message(_BytesStream(corrupted))


def test_payload_length_mismatch_is_rejected() -> None:
    # Header says 4x2 (32 bytes) but only 8 bytes follow.
    data = encode_message(mock_header(), b"\x00" * 8)
    with pytest.raises(ProtocolError, match="payload length"):
        read_message(_BytesStream(data))


def test_header_missing_a_required_field_is_a_protocol_error() -> None:
    header = mock_header()
    del header["seq"]
    with pytest.raises(ProtocolError, match="malformed header"):
        read_message(_BytesStream(encode_message(header, mock_payload())))


def test_bad_timestamp_is_a_protocol_error() -> None:
    header = mock_header()
    header["ts"] = "not-a-time"
    with pytest.raises(ProtocolError, match="malformed header"):
        read_message(_BytesStream(encode_message(header, mock_payload())))


def test_non_utf8_header_is_a_protocol_error() -> None:
    import struct

    from sensing.protocol import MAGIC, PREFIX_FORMAT

    bad = b"\xff\xfe{}"
    with pytest.raises(ProtocolError, match="UTF-8"):
        read_message(_BytesStream(struct.pack(PREFIX_FORMAT, MAGIC, len(bad), 0) + bad))


def test_listener_survives_a_malformed_message() -> None:
    from sensing.listener import TcpSnapshotListener

    received: list[Snapshot] = []
    done = threading.Event()

    def handler(snapshot: Snapshot) -> None:
        received.append(snapshot)
        done.set()

    listener = TcpSnapshotListener(port=0, handler=handler)
    thread = listener.start_background()
    try:
        bad = mock_header()
        del bad["seq"]
        broken = _connect(listener.port)
        broken.sendall(encode_message(bad, mock_payload()))
        broken.close()

        good = _connect(listener.port)
        good.sendall(encode_message(mock_header(seq=5), mock_payload()))
        assert done.wait(timeout=5), "listener stopped serving after a malformed message"
        good.close()
        assert thread.is_alive()
    finally:
        listener.shutdown()

    assert [s.seq for s in received] == [5]


class _BytesStream:
    """Minimal readable binary stream over a bytes buffer, for framing tests without a socket."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, count: int) -> bytes:
        chunk = self._data[self._pos : self._pos + count]
        self._pos += len(chunk)
        return chunk

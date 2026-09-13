"""Windows-only end-to-end test for the named-pipe transport.

Uses the same ctypes Win32 calls a client would, so it exercises the real ``NamedPipeSnapshotListener``
rather than a stand-in.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")

from sensing.codec import encode_message  # noqa: E402
from sensing.protocol import Snapshot  # noqa: E402
from tests.mock_snapshot import mock_header, mock_payload  # noqa: E402


def _write_all_to_pipe(path: str, data: bytes) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    invalid = ctypes.c_void_p(-1).value

    handle = None
    for _ in range(50):  # wait for the server instance to exist
        handle = kernel32.CreateFileW(
            ctypes.c_wchar_p(path), GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
        )
        if handle is not None and handle != invalid:
            break
        handle = None
        time.sleep(0.05)
    assert handle is not None, "could not open pipe"

    try:
        written = ctypes.c_ulong(0)
        buffer = (ctypes.c_char * len(data)).from_buffer_copy(data)
        ok = kernel32.WriteFile(handle, buffer, len(data), ctypes.byref(written), None)
        assert ok and written.value == len(data)
    finally:
        kernel32.CloseHandle(handle)


def test_named_pipe_end_to_end() -> None:
    from sensing.listener import NamedPipeSnapshotListener

    pipe_name = f"context-importance-test-{threading.get_ident()}"
    received: list[Snapshot] = []
    done = threading.Event()

    def handler(snapshot: Snapshot) -> None:
        received.append(snapshot)
        done.set()

    listener = NamedPipeSnapshotListener(pipe_name, handler)
    thread = listener.start_background()
    try:
        payload = mock_payload()
        _write_all_to_pipe(rf"\\.\pipe\{pipe_name}", encode_message(mock_header(seq=99), payload))
        assert done.wait(timeout=5), "listener did not receive the message"
    finally:
        listener.shutdown()
        thread.join(timeout=2)

    assert received[0].seq == 99
    assert received[0].payload == payload

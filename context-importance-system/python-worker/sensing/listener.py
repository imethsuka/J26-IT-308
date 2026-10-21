"""Listener that accepts collector connections and hands each decoded snapshot to a callback.

Two transports, chosen by config:
  * ``TcpSnapshotListener``   - loopback TCP, pure stdlib.
  * ``NamedPipeSnapshotListener`` - Windows named pipe via ctypes (no third-party dependency).

Both read framed messages (see ``protocol``) until the peer disconnects, then wait for the next
connection. Each decoded snapshot goes to a handler (``main.py`` installs
``pipeline.SnapshotPipeline``). The TCP listener calls the handler from a per-connection thread, so
a handler must be safe to call from a thread other than the one that created it.
"""

from __future__ import annotations

import io
import logging
import socket
import socketserver
import threading
from typing import Callable

from .codec import read_message
from .protocol import ProtocolError, Snapshot

logger = logging.getLogger("worker.sensing.ipc")

SnapshotHandler = Callable[[Snapshot], None]


def log_snapshot(snapshot: Snapshot) -> None:
    """Default handler: log a one-line summary. Never logs frame pixels."""
    frame = snapshot.frame
    frame_desc = "no-frame"
    if frame is not None:
        frame_desc = f"{frame.width}x{frame.height} {frame.format} {len(snapshot.payload)}B diff={frame.diff}"
    act = snapshot.activity or {}
    where = "excluded" if snapshot.excluded else f"{snapshot.app!r} domain={snapshot.context.get('domain')!r}"
    logger.info(
        "snapshot seq=%d %s dwell=%.1fs keys=%s clicks=%s scrolls=%s moves=%s idle=%s frame=[%s]",
        snapshot.seq,
        where,
        snapshot.context.get("dwell_seconds", 0.0),
        act.get("key_presses"),
        act.get("mouse_clicks"),
        act.get("mouse_scrolls"),
        act.get("mouse_moves"),
        snapshot.idle.get("is_idle"),
        frame_desc,
    )


def _drain_stream(stream: io.BufferedReader, handler: SnapshotHandler, peer: str) -> None:
    """Read messages until the peer disconnects."""
    while True:
        try:
            snapshot = read_message(stream)
        except EOFError:
            logger.debug("connection from %s closed", peer)
            return
        except ProtocolError:
            logger.exception("protocol error from %s; dropping connection", peer)
            return
        except OSError as exc:  # reset socket / failed pipe read: end this connection, keep serving
            logger.warning("read from %s failed (%s); dropping connection", peer, exc)
            return
        try:
            handler(snapshot)
        except Exception:  # a bad handler must not kill the listener
            logger.exception("snapshot handler raised")


class TcpSnapshotListener:
    """Loopback TCP listener. Binds 127.0.0.1 only, so it is unreachable from other hosts."""

    def __init__(self, port: int, handler: SnapshotHandler = log_snapshot, host: str = "127.0.0.1") -> None:
        self._handler = handler
        outer = self

        class _Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                peer = f"{self.client_address[0]}:{self.client_address[1]}"
                logger.info("collector connected from %s", peer)
                with self.request.makefile("rb") as stream:
                    _drain_stream(stream, outer._handler, peer)

        class _Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = _Server((host, port), _Handler)
        self.port = self._server.server_address[1]

    def serve_forever(self) -> None:
        logger.info("listening on tcp %s", self.port)
        self._server.serve_forever()

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="ipc-tcp", daemon=True)
        thread.start()
        return thread

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class NamedPipeSnapshotListener:
    """
    Windows named-pipe listener via the Win32 API (ctypes). One instance is served at a time,
    which is enough for a single local collector. Restricted to the local machine; the client
    connects with current-user-only, so a different user cannot connect.
    """

    _PIPE_ACCESS_INBOUND = 0x00000001
    _PIPE_TYPE_BYTE = 0x00000000
    _PIPE_READMODE_BYTE = 0x00000000
    _PIPE_WAIT = 0x00000000
    _PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
    _PIPE_UNLIMITED_INSTANCES = 255
    _ERROR_PIPE_CONNECTED = 535
    _ERROR_BROKEN_PIPE = 109

    def __init__(self, pipe_name: str, handler: SnapshotHandler = log_snapshot) -> None:
        import ctypes  # imported lazily so the module still imports on non-Windows for tests
        from ctypes import wintypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._path = rf"\\.\pipe\{pipe_name}"
        self._handler = handler
        self._stop = threading.Event()
        # HANDLE is pointer-sized. Without these prototypes ctypes assumes a 32-bit int return and
        # truncates the handle on 64-bit Python, so every later call gets an invalid handle.
        self._invalid_handle = ctypes.c_void_p(-1).value
        k = self._kernel32
        k.CreateNamedPipeW.restype = wintypes.HANDLE
        k.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        ]
        k.ConnectNamedPipe.restype = wintypes.BOOL
        k.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        k.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.ReadFile.restype = wintypes.BOOL
        k.ReadFile.argtypes = [
            wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
        ]

    def serve_forever(self) -> None:
        logger.info("listening on named pipe %s", self._path)
        while not self._stop.is_set():
            handle = self._create_instance()
            try:
                if not self._connect(handle):
                    continue
                logger.info("collector connected on %s", self._path)
                self._drain_pipe(handle)
            finally:
                self._kernel32.DisconnectNamedPipe(handle)
                self._kernel32.CloseHandle(handle)

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="ipc-pipe", daemon=True)
        thread.start()
        return thread

    def shutdown(self) -> None:
        self._stop.set()

    def _create_instance(self) -> int:
        handle = self._kernel32.CreateNamedPipeW(
            self._ctypes.c_wchar_p(self._path),
            self._PIPE_ACCESS_INBOUND,
            self._PIPE_TYPE_BYTE | self._PIPE_READMODE_BYTE | self._PIPE_WAIT | self._PIPE_REJECT_REMOTE_CLIENTS,
            self._PIPE_UNLIMITED_INSTANCES,
            0,
            64 * 1024,
            0,
            None,
        )
        if handle is None or handle == self._invalid_handle:
            raise OSError(self._ctypes.get_last_error(), "CreateNamedPipeW failed")
        return handle

    def _connect(self, handle: int) -> bool:
        if self._kernel32.ConnectNamedPipe(handle, None):
            return True
        err = self._ctypes.get_last_error()
        return err == self._ERROR_PIPE_CONNECTED

    def _drain_pipe(self, handle: int) -> None:
        stream = io.BufferedReader(_PipeRawReader(self._kernel32, handle, self._ctypes))
        _drain_stream(stream, self._handler, self._path)


class _PipeRawReader(io.RawIOBase):
    """Adapts a Win32 pipe handle to a readable binary stream for the shared decode path."""

    def __init__(self, kernel32, handle: int, ctypes_mod) -> None:
        self._kernel32 = kernel32
        self._handle = handle
        self._ctypes = ctypes_mod

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        read = self._ctypes.c_ulong(0)
        ok = self._kernel32.ReadFile(
            self._handle,
            (self._ctypes.c_char * len(buffer)).from_buffer(buffer),
            len(buffer),
            self._ctypes.byref(read),
            None,
        )
        if not ok:
            # Broken pipe = clean disconnect: signal EOF by returning 0.
            if self._ctypes.get_last_error() == NamedPipeSnapshotListener._ERROR_BROKEN_PIPE:
                return 0
            raise OSError(self._ctypes.get_last_error(), "ReadFile failed")
        return read.value


def create_listener(
    transport: str,
    *,
    pipe_name: str = "context-importance",
    tcp_port: int = 51789,
    handler: SnapshotHandler = log_snapshot,
):
    """Build a listener from config. ``transport`` is 'namedpipe' or 'tcp'."""
    transport = transport.lower()
    if transport == "namedpipe":
        return NamedPipeSnapshotListener(pipe_name, handler)
    if transport == "tcp":
        return TcpSnapshotListener(tcp_port, handler)
    raise ValueError(f"unknown transport {transport!r}")

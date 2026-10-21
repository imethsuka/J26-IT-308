"""Tests for ``pipeline.SnapshotPipeline`` (snapshot -> features -> score -> store) and the worker
entry point that wires it to the listener.
"""

from __future__ import annotations

import io
import socket
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from models.base import ImportanceScorer
from models.rule_based import RuleBasedImportanceScorer
from pipeline import SnapshotPipeline
from sensing.codec import encode_message, read_message
from storage.context_store import count_context_records, list_context_records
from storage.database import connect, migrate
from tests.mock_snapshot import mock_header, mock_payload

WORKER_DIR = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 9, 24, 7, 0, 0, tzinfo=timezone.utc)


class AlwaysSave(ImportanceScorer):
    name = "always-save"

    def predict_proba(self, features) -> float:
        return 1.0


def header(seq: int, *, seconds: float = 0.0, title: str = "Docs - Example", **kw) -> dict:
    h = mock_header(seq=seq, **kw)
    stamp = (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    h["ts"] = h["frame"]["captured_at"] = stamp
    if not h["context"]["excluded"]:
        h["context"]["title"] = title
    return h


def snapshot(seq: int, **kw):
    return read_message(io.BytesIO(encode_message(header(seq, **kw), mock_payload())))


@pytest.fixture
def conn():
    c = connect(":memory:")
    migrate(c)
    yield c
    c.close()


# --------------------------------------------------------------------------- handling


def test_stores_a_scored_unlabelled_record(conn) -> None:
    pipeline = SnapshotPipeline(conn, RuleBasedImportanceScorer(), embed=False)
    pipeline(snapshot(1))
    [record] = list_context_records(conn)
    assert record.app == "chrome" and record.title == "Docs - Example"
    assert record.decision in ("save", "discard")
    assert record.label is None and record.embedding is None


def test_excluded_snapshots_are_not_stored(conn) -> None:
    pipeline = SnapshotPipeline(conn, RuleBasedImportanceScorer(), embed=False)
    pipeline(snapshot(1, excluded=True))
    assert count_context_records(conn) == 0


def test_consecutive_frames_of_one_window_are_not_seen_before(conn) -> None:
    pipeline = SnapshotPipeline(conn, RuleBasedImportanceScorer(), embed=False)
    for seq in range(1, 5):
        pipeline(snapshot(seq, seconds=seq))  # same window, a screen change every second
    pipeline(snapshot(5, seconds=10, title="Other page"))
    pipeline(snapshot(6, seconds=20))  # back to the first page: a real revisit
    seen = [r.features["ctx_seen_before"] for r in list_context_records(conn)]
    assert seen == [0, 0, 0, 0, 0, 1]


def test_save_without_embedding_updates_time_since_last_save(conn) -> None:
    pipeline = SnapshotPipeline(conn, AlwaysSave(), embed=False)
    pipeline(snapshot(1, seconds=0))
    pipeline(snapshot(2, seconds=30))
    first, second = list_context_records(conn)
    assert first.features["tmp_has_prior_save"] == 0
    assert second.features["tmp_has_prior_save"] == 1
    assert second.features["tmp_time_since_last_save_seconds"] == 30.0


def test_snapshots_after_close_are_dropped(conn) -> None:
    pipeline = SnapshotPipeline(conn, RuleBasedImportanceScorer(), embed=False)
    pipeline.close()
    pipeline(snapshot(1))
    assert count_context_records(conn) == 0


# --------------------------------------------------------------------------- embedding


def test_encoder_load_failure_disables_embedding_once(conn, monkeypatch, caplog) -> None:
    import features.mobileclip_encoder as encoder_module

    calls = []

    def failing_loader():
        calls.append(1)
        raise FileNotFoundError("MobileCLIP-S0 checkpoint not given")

    monkeypatch.setattr(encoder_module, "default_encoder", failing_loader)
    pipeline = SnapshotPipeline(conn, RuleBasedImportanceScorer(), embed=True)
    with caplog.at_level("WARNING", logger="worker.pipeline"):
        for seq in (1, 2, 3):
            pipeline(snapshot(seq, seconds=seq))

    assert len(calls) == 1                                  # not retried on every frame
    assert pipeline.embedding_enabled is False
    assert sum("embedding disabled" in r.getMessage() for r in caplog.records) == 1
    assert count_context_records(conn) == 3                 # records are still stored
    assert all(r.embedding is None for r in list_context_records(conn))


def test_loaded_encoder_embeds_the_bgra_frame(conn, monkeypatch) -> None:
    import features.mobileclip_encoder as encoder_module

    seen_shapes = []

    class StubEncoder:
        def embed_frame(self, image):
            seen_shapes.append(image.shape)
            vector = np.ones(512, dtype=np.float32)
            return vector / np.linalg.norm(vector)

    monkeypatch.setattr(encoder_module, "default_encoder", lambda: StubEncoder())
    pipeline = SnapshotPipeline(conn, RuleBasedImportanceScorer(), embed=True)
    assert pipeline.load_encoder() is True
    pipeline(snapshot(1))
    assert seen_shapes == [(2, 4, 4)]                       # height x width x BGRA
    [record] = list_context_records(conn)
    assert record.embedding is not None and record.embedding.shape == (512,)


# --------------------------------------------------------------------------- listener threads


def test_tcp_listener_threads_can_store_records(tmp_path) -> None:
    from sensing.listener import TcpSnapshotListener

    db = tmp_path / "worker.db"
    conn = connect(db, check_same_thread=False)
    migrate(conn)
    reader = connect(db)
    pipeline = SnapshotPipeline(conn, RuleBasedImportanceScorer(), embed=False)
    listener = TcpSnapshotListener(0, handler=pipeline)
    listener.start_background()
    try:
        with socket.create_connection(("127.0.0.1", listener.port), timeout=5) as client:
            for seq in (1, 2, 3):
                client.sendall(encode_message(header(seq, seconds=seq), mock_payload()))
            deadline = time.monotonic() + 5
            while count_context_records(reader) < 3 and time.monotonic() < deadline:
                time.sleep(0.05)
    finally:
        listener.shutdown()
        pipeline.close()
        conn.close()
    try:
        assert count_context_records(reader) == 3
    finally:
        reader.close()


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
def test_worker_stops_on_ctrl_c_while_waiting_for_the_collector(tmp_path) -> None:
    # interrupt_main() raises KeyboardInterrupt in the main thread exactly as Ctrl+C does. With no
    # collector connected, the listener is blocked in ConnectNamedPipe the whole time.
    code = textwrap.dedent(f"""
        import sys, threading, _thread
        sys.argv = ["main.py", "--transport", "namedpipe", "--pipe-name", "cis-test-{time.time_ns()}",
                    "--db", r"{tmp_path / 'worker.db'}", "--no-embedding"]
        import main
        threading.Timer(2.0, _thread.interrupt_main).start()
        main.main()
        print("clean exit", flush=True)
    """)
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=WORKER_DIR, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "clean exit" in proc.stdout
    assert "shutting down" in proc.stderr


# --------------------------------------------------------------------------- shell surfaces


def test_shell_surfaces_are_not_stored(conn) -> None:
    pipeline = SnapshotPipeline(conn, RuleBasedImportanceScorer(), embed=False)
    for seq, app in enumerate(["dwm", "DWM.exe", "LockApp", "SearchHost", "ShellHost"], start=1):
        h = header(seq, seconds=seq)
        h["context"]["app"] = app
        pipeline(read_message(io.BytesIO(encode_message(h, mock_payload()))))
    assert count_context_records(conn) == 0


def test_real_apps_and_explorer_are_still_stored(conn) -> None:
    pipeline = SnapshotPipeline(conn, RuleBasedImportanceScorer(), embed=False)
    for seq, app in enumerate(["explorer", "Code", "ApplicationFrameHost"], start=1):
        h = header(seq, seconds=seq)
        h["context"]["app"] = app
        pipeline(read_message(io.BytesIO(encode_message(h, mock_payload()))))
    assert count_context_records(conn) == 3

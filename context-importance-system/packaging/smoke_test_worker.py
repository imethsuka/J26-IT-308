"""End-to-end check of the FROZEN worker: start worker.exe, send it frames over the named pipe as the collector would,
and verify what lands in the database. Uses a throw-away data folder; never touches your real data.

    python smoke_test_worker.py --dist dist --onnx <path to mobileclip_s0_image.onnx> [--frames 3]
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python-worker"))
import numpy as np  # noqa: E402

from sensing.codec import encode_message  # noqa: E402
from tests.mock_snapshot import mock_header  # noqa: E402


PIPE_ROOT = r"\\.\pipe" + "\\"


def send(pipe_path: str, data: bytes) -> None:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    invalid = ctypes.c_void_p(-1).value
    handle = None
    for _ in range(200):
        h = k.CreateFileW(pipe_path, 0x40000000, 0, None, 3, 0, None)
        if h not in (None, invalid):
            handle = h
            break
        time.sleep(0.1)
    assert handle is not None, "worker pipe never appeared"
    written = ctypes.c_ulong(0)
    buf = (ctypes.c_char * len(data)).from_buffer_copy(data)
    assert k.WriteFile(handle, buf, len(data), ctypes.byref(written), None) and written.value == len(data)
    k.CloseHandle(handle)


def rss_mb(pid: int) -> float:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {pid}).WorkingSet64/1MB"], capture_output=True, text=True).stdout
    return float(out.strip() or 0)


def check_screen_text(dist: Path, tmp: Path, env: dict, pipe: str, proc, log) -> bool:
    """Opted in: text is buffered encrypted in its own file and the job turns it into entries. Opted out: nothing is kept."""
    data = tmp / "context-importance"
    raw_db, mem_db, ctx_db = data / "raw_buffer.db", data / "memory.db", data / "context.db"
    good = True

    def check(label: str, condition: bool) -> None:
        nonlocal good
        print(f"  [{'ok' if condition else 'FAIL'}] {label}")
        good = good and condition

    print("screen text:")
    for _ in range(100):
        if raw_db.exists():
            break
        time.sleep(0.1)
    check("raw_buffer.db was created beside context.db", raw_db.exists())
    if raw_db.exists():
        con = sqlite3.connect(f"file:{raw_db}?mode=ro", uri=True)
        n = con.execute("SELECT COUNT(*) FROM raw_captures WHERE text_blob IS NOT NULL").fetchone()[0]
        check(f"{n} captures buffered with text", n >= 3)
        check("no plain text in the file", b"smokeword" not in raw_db.read_bytes())
        con.close()
    check("context.db holds no screen text", b"smokeword" not in ctx_db.read_bytes())

    # Opted out: a worker told --no-screen-text must keep nothing even if it is sent text.
    proc.terminate()
    proc.wait(10)
    before = sqlite3.connect(f"file:{raw_db}?mode=ro", uri=True).execute("SELECT COUNT(*) FROM raw_captures").fetchone()[0]
    out_pipe = pipe + "-out"
    out = subprocess.Popen([str(dist / "worker" / "worker.exe"), "--transport", "namedpipe", "--pipe-name", out_pipe, "--no-screen-text"],
                           env=env, stdout=log, stderr=log, cwd=str(tmp))
    try:
        h = mock_header(50, width=64, height=36)
        h["version"] = 2
        h["ts"] = "2026-10-19T11:00:00.000000Z"
        h["text"] = {"source": "uia", "chars": 0, "truncated": False, "read_ms": 1.0, "content": "optedout " * 20}
        send(PIPE_ROOT + out_pipe, encode_message(h, bytes(64 * 36 * 4)))
        time.sleep(3)
        after = sqlite3.connect(f"file:{raw_db}?mode=ro", uri=True).execute("SELECT COUNT(*) FROM raw_captures").fetchone()[0]
        check("opted out (--no-screen-text): nothing was buffered", after == before)
    finally:
        out.terminate()
        out.wait(10)

    # The job (adapt.exe): no filter -> metadata-only entries; the raw rows are consumed.
    adapt = dist / "worker" / "adapt.exe"
    run = subprocess.run([str(adapt), "--db", str(ctx_db), "--summary-log", str(tmp / "runs.jsonl"), "--force", "--no-require-ac",
                          "--screen-memory"], env=env, capture_output=True, text=True, cwd=str(tmp), timeout=300)
    check(f"adapt.exe --screen-memory exited {run.returncode}", run.returncode == 0)
    check("memory.db was created", mem_db.exists())
    if mem_db.exists():
        con = sqlite3.connect(f"file:{mem_db}?mode=ro", uri=True)
        entries = con.execute("SELECT tier, text_persisted FROM memory_entries").fetchall()
        check(f"{len(entries)} memory entries, none with text (no privacy filter installed)", entries and all(t == 0 for _, t in entries))
        check("no screen text in memory.db", b"smokeword" not in mem_db.read_bytes())
        con.close()
    left = sqlite3.connect(f"file:{raw_db}?mode=ro", uri=True).execute("SELECT COUNT(*) FROM raw_captures").fetchone()[0]
    print(f"  raw rows left after the job: {left}")
    return good


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--frames", type=int, default=3)
    ap.add_argument("--screen-text", action="store_true",
                    help="also check opt-in/opt-out screen text: raw_buffer.db, then adapt.exe --screen-memory -> memory.db")
    args = ap.parse_args()

    dist = Path(args.dist).resolve()
    worker = dist / "worker" / "worker.exe"
    models = dist / "models"
    models.mkdir(exist_ok=True)
    shutil.copy2(args.onnx, models / "mobileclip_s0_image.onnx")  # the installer puts it here

    tmp = Path(tempfile.mkdtemp(prefix="ctx-smoke-"))
    pipe = f"ctx-smoke-{os.getpid()}"
    env = {k: v for k, v in os.environ.items() if k not in ("MOBILECLIP_S0_CHECKPOINT", "MOBILECLIP_ONNX_MODEL", "CONTEXT_IMPORTANCE_DATA")}
    env["LOCALAPPDATA"] = str(tmp)  # a frozen worker keeps its data here
    log = open(tmp / "worker.log", "wb")
    proc = subprocess.Popen([str(worker), "--transport", "namedpipe", "--pipe-name", pipe], env=env, stdout=log, stderr=log, cwd=str(tmp))
    ok = True
    try:
        rng = np.random.default_rng(0)
        width, height = 1920, 1080
        for seq in range(1, args.frames + 1):
            header = mock_header(seq, width=width, height=height)
            header["ts"] = f"2026-10-19T10:00:{seq:02d}.000000Z"
            header["context"]["title"] = f"Smoke test {seq}"
            if args.screen_text:
                header["version"] = 2
                header["text"] = {"source": "uia", "chars": 0, "truncated": False, "read_ms": 1.0,
                                  "content": "".join(f"smokeword{i} " for i in range(seq * 10, seq * 10 + 30))}
            payload = rng.integers(0, 256, width * height * 4, dtype=np.uint8).tobytes()
            send(PIPE_ROOT + pipe, encode_message(header, payload))
        # A Windows shell surface (the compositor) is not a context: the worker must skip it.
        shell = mock_header(99, width=width, height=height)
        shell["ts"] = "2026-10-19T10:00:59.000000Z"
        shell["context"]["app"] = "dwm"
        send(PIPE_ROOT + pipe, encode_message(shell, payload))
        db = tmp / "context-importance" / "context.db"
        for _ in range(200):
            if db.exists():
                try:
                    n = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute("SELECT COUNT(*) FROM context_records").fetchone()[0]
                except sqlite3.Error:
                    n = 0
                if n >= args.frames:
                    break
            time.sleep(0.1)
        time.sleep(1)
        used = rss_mb(proc.pid)
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("SELECT decision, embedding IS NOT NULL, embedding_dim, drc_would_capture, length(features_json) FROM context_records").fetchall()
        ver = con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        print(f"worker.exe alive: {proc.poll() is None} | data folder: {db.parent.name}/{db.name} (schema v{ver})")
        print(f"records stored: {len(rows)} | with 512-d embedding: {sum(1 for r in rows if r[1] and r[2] == 512)} | drc verdict recorded: {sum(1 for r in rows if r[3] is not None)}")
        print(f"worker working set after {args.frames} 1080p frames: {used:.0f} MB")
        ok = len(rows) == args.frames and all(r[1] and r[2] == 512 for r in rows)
        if args.screen_text:
            ok = check_screen_text(dist, tmp, env, pipe, proc, log) and ok
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        text = (tmp / "worker.log").read_text(errors="replace")
        print("--- worker log (tail) ---\n" + "\n".join(text.splitlines()[-8:]))
        shutil.rmtree(tmp, ignore_errors=True)
    print("SMOKE TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

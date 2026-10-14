"""
CAPRLM screenshot dataset processing script.

Processes screenshots through the OCR → detection → feature-extraction pipeline
and writes results incrementally as CANDIDATE records to a separate CSV.

IMPORTANT — separation of concerns
------------------------------------
Output rows carry an ``auto_label_candidate`` column produced by the rule-based
baseline.  This is NEVER equivalent to a manually verified ground-truth label.
The existing annotation CSV (CAPRLM_annotation_50.csv) is never modified.

Usage examples
--------------
# Pilot: process 10 images, batch size 5
python -m backend.scripts.process_screenshots \\
    --input-dir C:/screenshots --limit 10 --batch-size 5

# Resume a prior interrupted run
python -m backend.scripts.process_screenshots --input-dir C:/screenshots

# With OCR timeout (recommended for full-dataset runs on Windows)
python -m backend.scripts.process_screenshots \\
    --input-dir C:/screenshots --ocr-timeout 120

# Save raw OCR text to a restricted sub-directory (disabled by default)
python -m backend.scripts.process_screenshots \\
    --input-dir C:/screenshots --save-ocr-text
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing
import queue as _queue_module
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup — allow `python -m backend.scripts.process_screenshots`
# ---------------------------------------------------------------------------
_MEMBER2_DIR = Path(__file__).resolve().parent.parent.parent
if str(_MEMBER2_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMBER2_DIR))

from backend.services.sensitive_detection import detect_sensitive_entities  # noqa: E402
from backend.services.feature_extraction import extract_features             # noqa: E402
from backend.services.rule_baseline import predict_rule_based                # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_BATCH_SIZE: int = 10
_DEFAULT_OUTPUT_DIR: Path = _MEMBER2_DIR / "backend" / "data" / "processing_output"

# The annotation CSV that must never be overwritten
_PROTECTED_CSV: Path = _MEMBER2_DIR / "backend" / "data" / "CAPRLM_annotation_50.csv"

_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}
)

_CANDIDATES_FILENAME = "candidates.csv"
_MANIFEST_FILENAME   = "manifest.json"
_OCR_TEXT_SUBDIR     = "ocr_text"

# Status values stored in the manifest
_STATUS_DONE    = "done"
_STATUS_FAILED  = "failed"

# Default per-image OCR timeout when --ocr-timeout is used.
# Based on benchmark data: typical images take 8–30 s; the pathological
# outlier took 1 337 s.  120 s gives a 4× safety margin over normal images.
_DEFAULT_OCR_TIMEOUT_S: int = 120

# Candidate CSV column order — note: no 'privacy_risk_label' column; auto
# candidates are labelled 'auto_label_candidate' to prevent confusion with
# manually verified ground truth.
_CSV_HEADER: list[str] = [
    "screenshot_id",
    "application_context",
    "EMAIL",
    "PHONE",
    "URL",
    "IP_ADDRESS",
    "DATE",
    "WINDOWS_PATH",
    "sensitive_entity_present",
    "auto_label_candidate",
    "ocr_text_length",
    "email_count",
    "phone_count",
    "url_count",
    "ip_count",
    "date_count",
    "windows_path_count",
    "elapsed_seconds",
    "processed_at",
]


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _load_manifest(manifest_path: Path) -> dict[str, dict]:
    """Load manifest from disk or return empty dict on missing / corrupt file."""
    if manifest_path.exists():
        try:
            with manifest_path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_manifest(manifest_path: Path, manifest: dict[str, dict]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _record_manifest_entry(
    manifest: dict[str, dict],
    screenshot_id: str,
    status: str,
    elapsed: float,
    error: str | None = None,
    output_row_index: int | None = None,
) -> None:
    """Update the manifest dict in-memory only.

    The caller (run()) flushes to disk at each batch boundary via
    _save_manifest(), reducing O(n²) per-image writes to O(n/batch_size).

    Recovery note: a mid-batch interruption leaves images processed within
    that batch absent from the on-disk manifest.  On the next run those
    images are re-processed; the candidate CSV may contain at most
    (batch_size - 1) duplicate rows for the interrupted batch.
    """
    manifest[screenshot_id] = {
        "screenshot_id": screenshot_id,
        "status": status,
        "error": error,
        "elapsed_seconds": round(elapsed, 4),
        "output_row_index": output_row_index,
        "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ---------------------------------------------------------------------------
# Candidates CSV helpers
# ---------------------------------------------------------------------------

def _ensure_candidates_header(candidates_path: Path) -> None:
    """Write the CSV header if the file does not exist yet."""
    if not candidates_path.exists():
        candidates_path.parent.mkdir(parents=True, exist_ok=True)
        with candidates_path.open("w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=_CSV_HEADER).writeheader()


def _append_candidate_row(candidates_path: Path, row: dict[str, Any]) -> None:
    """Append a single row to the candidates CSV (incremental write)."""
    with candidates_path.open("a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=_CSV_HEADER).writerow(row)


# ---------------------------------------------------------------------------
# Image discovery
# ---------------------------------------------------------------------------

def _discover_images(input_dir: Path) -> list[Path]:
    """Return a sorted list of image paths in input_dir (non-recursive)."""
    return sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS
    )


# ---------------------------------------------------------------------------
# OCR worker process — runs in a SEPARATE PROCESS so the OS can terminate it
# ---------------------------------------------------------------------------

def _ocr_worker_fn(
    task_queue: Any,
    result_queue: Any,
) -> None:
    """
    OCR worker process entry point.

    Must be a module-level function so that multiprocessing's 'spawn' start
    method (the only option on Windows) can pickle and import it in the
    child process.

    Protocol
    --------
    Startup  → result_queue.put(("ready", None))
    Per-image task_queue item : str(image_path)
    Success  → result_queue.put(("ok", (text, detection_count)))
    OCR error→ result_queue.put(("error", "<ExcType>: <msg>"))
    Shutdown : task_queue.put(None)  (sentinel)
    """
    try:
        import easyocr  # noqa: PLC0415
        reader = easyocr.Reader(["en"])
        result_queue.put(("ready", None))
    except Exception as exc:  # noqa: BLE001
        result_queue.put(("init_error", f"{type(exc).__name__}: {exc}"))
        return

    while True:
        item = task_queue.get()
        if item is None:
            break
        try:
            detections = reader.readtext(item, canvas_size=1536, mag_ratio=0.8)
            text = "\n".join(det[1] for det in detections)
            result_queue.put(("ok", (text, len(detections))))
        except Exception as exc:  # noqa: BLE001
            result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


class _OcrWorker:
    """
    Persistent subprocess that owns the EasyOCR reader and enforces a
    per-image wall-clock timeout.

    On timeout the worker process is terminated (releasing its ~600 MB of
    model RAM) and automatically restarted.  Restart takes ~20 s on CPU
    because EasyOCR must reload the model weights.

    The timed-out image is NOT written to the candidate CSV; run() records
    it as status=failed so it is retried on the next run.

    Windows note: signal.alarm is unavailable on Windows; this subprocess
    approach is the correct alternative.  multiprocessing.get_context("spawn")
    is used explicitly because "spawn" is the only start method available on
    Windows and is the default in CPython ≥ 3.12.
    """

    _INIT_TIMEOUT_S: int = 90  # EasyOCR model load can take up to 30 s on CPU

    def __init__(self, timeout_s: int) -> None:
        self._timeout_s = timeout_s
        self._ctx = multiprocessing.get_context("spawn")
        self._process: Any = None
        self._task_q: Any = None
        self._result_q: Any = None
        self._start()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start(self) -> None:
        """Start (or restart after timeout) the worker process."""
        self._task_q   = self._ctx.Queue()
        self._result_q = self._ctx.Queue()
        self._process  = self._ctx.Process(
            target=_ocr_worker_fn,
            args=(self._task_q, self._result_q),
            daemon=True,
        )
        self._process.start()
        try:
            status, payload = self._result_q.get(timeout=self._INIT_TIMEOUT_S)
        except _queue_module.Empty:
            self._kill()
            raise RuntimeError(
                f"OCR worker did not start within {self._INIT_TIMEOUT_S}s. "
                "Check that EasyOCR and its model weights are installed correctly."
            )
        if status == "init_error":
            self._kill()
            raise RuntimeError(f"OCR worker initialization failed: {payload}")

    def _kill(self) -> None:
        """Forcefully terminate the worker process."""
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
            self._process.join(5)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(2)

    def shutdown(self) -> None:
        """Signal a clean shutdown and wait for the worker to exit."""
        if self._process is not None and self._process.is_alive():
            try:
                self._task_q.put(None)   # shutdown sentinel
                self._process.join(10)
            finally:
                if self._process.is_alive():
                    self._kill()

    # ── OCR call ─────────────────────────────────────────────────────────────

    def run_ocr(self, image_path: Path) -> tuple[str, int]:
        """
        Run OCR in the worker process, blocking for at most timeout_s seconds.

        On success  : returns (extracted_text, detection_count)
        On timeout  : terminates and restarts the worker, then raises TimeoutError
        On OCR error: raises RuntimeError with the worker's error message

        The caller (run()) wraps this in try/except Exception, which catches
        both TimeoutError and RuntimeError, records the image as failed, and
        continues with the next image.
        """
        self._task_q.put(str(image_path))
        try:
            status, payload = self._result_q.get(timeout=self._timeout_s)
        except _queue_module.Empty:
            print(
                f"    TIMEOUT {image_path.name} after {self._timeout_s}s — "
                "terminating worker, restarting (adds ~20s overhead) …"
            )
            self._kill()
            self._start()
            raise TimeoutError(
                f"OCR timed out after {self._timeout_s}s on {image_path.name}"
            )
        if status == "error":
            raise RuntimeError(payload)
        return payload  # (text, detection_count)


# ---------------------------------------------------------------------------
# OCR helpers — reader initialized once per run for performance
# ---------------------------------------------------------------------------

def _init_ocr_reader() -> Any:
    """
    Initialize and return an EasyOCR Reader.

    This is deliberately kept separate from _run_ocr so that tests can patch
    only the initialization without affecting other logic.
    Creating the reader is expensive (~2–5 s on CPU); it is done once per run.
    """
    try:
        import easyocr  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "EasyOCR is not installed. "
            "Install it with: pip install easyocr"
        ) from exc
    return easyocr.Reader(["en"])


def _run_ocr(reader: Any, image_path: Path) -> tuple[str, int]:
    """
    Run OCR on one image using a pre-initialized reader.

    Replicates the output contract of ocr_service.extract_text without
    re-creating the Reader object for every image.

    Returns (extracted_text, detections_count).
    """
    detections = reader.readtext(str(image_path), canvas_size=1536, mag_ratio=0.8)
    extracted_text = "\n".join(det[1] for det in detections)
    return extracted_text, len(detections)


# ---------------------------------------------------------------------------
# Single-image processing
# ---------------------------------------------------------------------------

def _process_one(
    reader: Any,
    image_path: Path,
    application_context: str = "Unknown",
    save_ocr_text: bool = False,
    ocr_text_dir: Path | None = None,
    ocr_fn: Callable[[Path], tuple[str, int]] | None = None,
) -> dict[str, Any]:
    """
    Run the full pipeline for one image and return a candidate row dict.

    Parameters
    ----------
    reader:
        Pre-initialized EasyOCR reader.  Used when ocr_fn is None.
    ocr_fn:
        Optional callable (Path) -> (text, count).  When provided, it is
        called instead of _run_ocr(reader, path).  Used by run() when
        ocr_timeout_s is set (the callable is _OcrWorker.run_ocr).

    OCR text is NOT logged or printed.  It is optionally saved to a
    restricted directory only when save_ocr_text=True.

    Raises on any unrecoverable failure (caller handles and marks as failed).
    """
    if ocr_fn is not None:
        ocr_text, _detections_count = ocr_fn(image_path)
    else:
        ocr_text, _detections_count = _run_ocr(reader, image_path)

    # Privacy guard: only write OCR text when explicitly requested
    if save_ocr_text and ocr_text_dir is not None:
        ocr_text_dir.mkdir(parents=True, exist_ok=True)
        (ocr_text_dir / f"{image_path.stem}.txt").write_text(ocr_text, encoding="utf-8")

    detection_output = detect_sensitive_entities(ocr_text)
    features = extract_features(ocr_text, detection_output, application_context)
    rule_result = predict_rule_based(features)

    return {
        "screenshot_id":       image_path.name,
        "application_context": application_context,
        "EMAIL":               int(features["email_count"]        > 0),
        "PHONE":               int(features["phone_count"]        > 0),
        "URL":                 int(features["url_count"]          > 0),
        "IP_ADDRESS":          int(features["ip_count"]           > 0),
        "DATE":                int(features["date_count"]         > 0),
        "WINDOWS_PATH":        int(features["windows_path_count"] > 0),
        "sensitive_entity_present": int(features["sensitive_entity_count"] > 0),
        "auto_label_candidate": rule_result["risk_level"],
        "ocr_text_length":     features["ocr_text_length"],
        "email_count":         features["email_count"],
        "phone_count":         features["phone_count"],
        "url_count":           features["url_count"],
        "ip_count":            features["ip_count"],
        "date_count":          features["date_count"],
        "windows_path_count":  features["windows_path_count"],
        # elapsed_seconds and processed_at are filled by the caller
        "elapsed_seconds":     None,
        "processed_at":        None,
    }


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------

def _report_progress(
    done: int,
    failed: int,
    total: int,
    job_start: float,
) -> None:
    processed = done + failed
    elapsed = time.monotonic() - job_start
    rate = processed / elapsed if elapsed > 0 else 0.0
    remaining = total - processed
    eta_s = remaining / rate if rate > 0 else float("inf")
    eta_str = f"{eta_s:.0f}s" if eta_s != float("inf") else "unknown"
    print(
        f"  Progress: {processed}/{total} | done={done} failed={failed} | "
        f"{rate:.2f} img/s | ETA {eta_str}"
    )


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def run(
    input_dir: Path,
    output_dir: Path,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    application_context: str = "Unknown",
    save_ocr_text: bool = False,
    ocr_timeout_s: int | None = None,
) -> dict[str, Any]:
    """
    Process screenshots and return a summary dict.

    Parameters
    ----------
    input_dir:
        Directory containing screenshot image files.
    output_dir:
        Directory where candidates.csv and manifest.json are written.
    batch_size:
        Number of images per batch (for progress reporting and manifest flush).
    limit:
        If set, process at most this many images (pilot mode).
    application_context:
        Context label applied to every image.
    save_ocr_text:
        If True, raw OCR text is written to <output_dir>/ocr_text/.
        Disabled by default to avoid storing sensitive content.
    ocr_timeout_s:
        Per-image wall-clock timeout in seconds.  When None (default), OCR
        runs in the main process with no timeout — the existing behaviour.
        When set, a persistent worker subprocess enforces the timeout:
        - Timed-out images are recorded as status=failed and retried on the
          next run.
        - On timeout the worker is restarted (~20 s overhead).
        - signal.alarm is NOT used; this approach works on Windows.
        Recommended value: _DEFAULT_OCR_TIMEOUT_S (120 s).

    Returns
    -------
    Summary dict with keys: processed, failed, skipped, total,
    elapsed_seconds, throughput_img_per_s, candidates_csv, manifest.
    """
    candidates_path = output_dir / _CANDIDATES_FILENAME
    manifest_path   = output_dir / _MANIFEST_FILENAME
    ocr_text_dir    = (output_dir / _OCR_TEXT_SUBDIR) if save_ocr_text else None

    # Hard safety check — never overwrite the protected annotation CSV
    if candidates_path.resolve() == _PROTECTED_CSV.resolve():
        raise ValueError(
            "Output path would overwrite the protected annotation CSV. "
            "Choose a different output directory."
        )

    manifest = _load_manifest(manifest_path)
    _ensure_candidates_header(candidates_path)

    images = _discover_images(input_dir)
    if not images:
        print(f"No supported images found in: {input_dir}")
        return {"processed": 0, "failed": 0, "skipped": 0, "total": 0}

    # Apply limit for pilot runs BEFORE filtering already-done images
    if limit is not None:
        images = images[:limit]

    # Resume: skip images already successfully completed
    pending = [
        img for img in images
        if manifest.get(img.name, {}).get("status") != _STATUS_DONE
    ]
    skipped = len(images) - len(pending)
    total   = len(images)

    print(f"Input dir   : {input_dir}")
    print(f"Output dir  : {output_dir}")
    print(f"Total images: {total} | Already done: {skipped} | To process: {len(pending)}")
    print(
        f"Batch size  : {batch_size} | Pilot limit: {limit} | "
        f"OCR text saved: {save_ocr_text} | OCR timeout: "
        f"{f'{ocr_timeout_s}s' if ocr_timeout_s is not None else 'none'}"
    )
    print()

    if not pending:
        print("All images already processed. Nothing to do.")
        done_count  = sum(1 for e in manifest.values() if e.get("status") == _STATUS_DONE)
        fail_count  = sum(1 for e in manifest.values() if e.get("status") == _STATUS_FAILED)
        return {
            "processed": done_count, "failed": fail_count,
            "skipped": skipped, "total": total,
        }

    # ── Initialize OCR (reader in main process, or worker subprocess) ────────
    worker: _OcrWorker | None = None
    ocr_fn: Callable[[Path], tuple[str, int]] | None = None
    reader: Any = None

    if ocr_timeout_s is not None:
        print(
            f"Starting OCR worker process (timeout: {ocr_timeout_s}s/image) …\n"
            f"  Worker restart on timeout adds ~20 s overhead per event.\n"
        )
        t_init = time.monotonic()
        worker = _OcrWorker(timeout_s=ocr_timeout_s)
        print(f"OCR worker ready in {time.monotonic() - t_init:.1f}s\n")
        ocr_fn = worker.run_ocr
    else:
        print("Initializing OCR reader (CPU, no GPU) …")
        t_init = time.monotonic()
        reader = _init_ocr_reader()
        print(f"OCR reader ready in {time.monotonic() - t_init:.1f}s\n")

    done_count = sum(1 for e in manifest.values() if e.get("status") == _STATUS_DONE)
    fail_count = sum(1 for e in manifest.values() if e.get("status") == _STATUS_FAILED)
    job_start  = time.monotonic()
    row_index  = done_count  # 0-based index for new rows in this run

    try:
        for batch_start in range(0, len(pending), batch_size):
            batch = pending[batch_start : batch_start + batch_size]
            print(
                f"Batch {batch_start // batch_size + 1}: "
                f"images {batch_start + 1}–{batch_start + len(batch)} of {len(pending)}"
            )

            for image_path in batch:
                screenshot_id = image_path.name
                t0 = time.monotonic()

                try:
                    row = _process_one(
                        reader, image_path,
                        application_context=application_context,
                        save_ocr_text=save_ocr_text,
                        ocr_text_dir=ocr_text_dir,
                        ocr_fn=ocr_fn,
                    )
                    elapsed = time.monotonic() - t0
                    row["elapsed_seconds"] = round(elapsed, 4)
                    row["processed_at"]    = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

                    _append_candidate_row(candidates_path, row)
                    _record_manifest_entry(
                        manifest, screenshot_id,
                        status=_STATUS_DONE,
                        elapsed=elapsed,
                        output_row_index=row_index,
                    )
                    done_count += 1
                    row_index  += 1

                except Exception as exc:  # noqa: BLE001
                    elapsed   = time.monotonic() - t0
                    error_msg = f"{type(exc).__name__}: {exc}"
                    _record_manifest_entry(
                        manifest, screenshot_id,
                        status=_STATUS_FAILED,
                        elapsed=elapsed,
                        error=error_msg,
                    )
                    fail_count += 1
                    print(f"    FAILED {screenshot_id}: {error_msg}")

            _save_manifest(manifest_path, manifest)
            _report_progress(done_count, fail_count, total, job_start)
            print()

    finally:
        if worker is not None:
            worker.shutdown()

    total_elapsed = time.monotonic() - job_start
    n_processed   = len(pending)
    throughput    = n_processed / total_elapsed if total_elapsed > 0 else 0.0

    print("=" * 60)
    print(f"Run complete — {n_processed} images in {total_elapsed:.1f}s")
    print(f"  Done       : {done_count}")
    print(f"  Failed     : {fail_count}")
    print(f"  Skipped    : {skipped} (already done in a prior run)")
    print(f"  Throughput : {throughput:.2f} img/s")
    print(f"  Candidates : {candidates_path}")
    print(f"  Manifest   : {manifest_path}")
    if save_ocr_text:
        print(f"  OCR text   : {ocr_text_dir}")

    return {
        "processed":           done_count,
        "failed":              fail_count,
        "skipped":             skipped,
        "total":               total,
        "elapsed_seconds":     round(total_elapsed, 2),
        "throughput_img_per_s": round(throughput, 4),
        "candidates_csv":      str(candidates_path),
        "manifest":            str(manifest_path),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Process screenshots through the CAPRLM pipeline. "
            "Writes CANDIDATE records only — not verified ground truth. "
            "The existing annotation CSV is never modified."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-dir", required=True, type=Path,
        metavar="DIR",
        help="Directory containing .jpg/.png/.bmp/.tiff screenshots.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR,
        metavar="DIR",
        help=f"Directory for output files (default: {_DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--batch-size", type=int, default=_DEFAULT_BATCH_SIZE,
        metavar="N",
        help=f"Images per batch (default: {_DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        metavar="N",
        help="Process at most N images — use for pilot runs (e.g. --limit 10).",
    )
    parser.add_argument(
        "--application-context", type=str, default="Unknown",
        metavar="CTX",
        help="Application context label applied to all images (default: Unknown).",
    )
    parser.add_argument(
        "--save-ocr-text", action="store_true",
        help=(
            "Save raw OCR text to <output-dir>/ocr_text/. "
            "DISABLED by default to avoid persisting sensitive content."
        ),
    )
    parser.add_argument(
        "--ocr-timeout", type=int, default=None,
        metavar="SECONDS",
        help=(
            f"Per-image OCR timeout in seconds (default: no timeout). "
            f"Recommended: {_DEFAULT_OCR_TIMEOUT_S}. "
            "Timed-out images are recorded as failed and retried on the next run. "
            "Uses a worker subprocess — the only safe timeout mechanism on Windows. "
            "Each timeout event triggers a worker restart (~20 s overhead)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    if not args.input_dir.is_dir():
        print(f"ERROR: --input-dir is not a directory: {args.input_dir}", file=sys.stderr)
        sys.exit(1)

    run(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        limit=args.limit,
        application_context=args.application_context,
        save_ocr_text=args.save_ocr_text,
        ocr_timeout_s=args.ocr_timeout,
    )


if __name__ == "__main__":
    main()

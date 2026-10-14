"""
Tests for backend/scripts/process_screenshots.py.

All tests mock OCR initialization and execution so that EasyOCR and GPU
resources are never required.  Real file I/O (CSV, manifest, image stubs)
is performed in tmp_path.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import backend.scripts.process_screenshots as ps


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_ocr(monkeypatch):
    """
    Replace OCR with a lightweight stub for all tests in this module.

    _init_ocr_reader  → returns a sentinel (not used directly)
    _run_ocr          → returns ("sample text email@example.com", 3)
    """
    monkeypatch.setattr(ps, "_init_ocr_reader", lambda: "mock-reader")
    monkeypatch.setattr(
        ps, "_run_ocr",
        lambda reader, path: ("sample text email@example.com", 3),
    )


def _make_images(directory: Path, names: list[str]) -> list[Path]:
    """Write minimal fake JPEG stubs to directory and return paths."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        p = directory / name
        p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        paths.append(p)
    return paths


def _read_candidates(candidates_path: Path) -> list[dict]:
    """Return all data rows from the candidates CSV."""
    with candidates_path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _load_manifest(manifest_path: Path) -> dict:
    with manifest_path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Helper: run with shared paths
# ---------------------------------------------------------------------------

def _run(input_dir, output_dir, **kwargs):
    return ps.run(input_dir=input_dir, output_dir=output_dir, **kwargs)


# ---------------------------------------------------------------------------
# Basic smoke test
# ---------------------------------------------------------------------------

def test_basic_run_produces_candidates_and_manifest(tmp_path):
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["a.jpg", "b.jpg"])

    result = _run(img_dir, out_dir)

    assert result["processed"] == 2
    assert result["failed"] == 0
    assert result["skipped"] == 0

    candidates = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    assert len(candidates) == 2

    manifest = _load_manifest(out_dir / ps._MANIFEST_FILENAME)
    assert len(manifest) == 2
    for entry in manifest.values():
        assert entry["status"] == "done"


# ---------------------------------------------------------------------------
# Resume behaviour
# ---------------------------------------------------------------------------

def test_resume_skips_already_done_images(tmp_path):
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["a.jpg", "b.jpg", "c.jpg"])

    # First run: process only a.jpg and b.jpg via limit=2
    result1 = _run(img_dir, out_dir, limit=2)
    assert result1["processed"] == 2

    # Second run: no limit — should process c.jpg and skip a.jpg, b.jpg
    result2 = _run(img_dir, out_dir)
    assert result2["skipped"] == 2
    assert result2["processed"] == 3  # cumulative: 2 prior + 1 new

    candidates = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    assert len(candidates) == 3  # three distinct rows


def test_resume_adds_no_extra_rows_when_all_done(tmp_path):
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["x.jpg"])

    _run(img_dir, out_dir)
    _run(img_dir, out_dir)  # second run: nothing to do

    candidates = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    assert len(candidates) == 1  # still one row, not two


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------

def test_duplicate_prevention_same_run(tmp_path):
    """Even if the same image appears twice (via limit spanning it twice),
    the manifest ensures no duplicate rows on the second pass."""
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["img.jpg"])

    _run(img_dir, out_dir)
    _run(img_dir, out_dir)  # repeated run

    rows = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    assert len(rows) == 1

    ids = [r["screenshot_id"] for r in rows]
    assert ids.count("img.jpg") == 1


# ---------------------------------------------------------------------------
# Failed image handling
# ---------------------------------------------------------------------------

def test_failed_image_does_not_stop_other_images(tmp_path, monkeypatch):
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["good.jpg", "bad.jpg", "also_good.jpg"])

    call_count = {"n": 0}

    def _selective_ocr(reader, path):
        call_count["n"] += 1
        if path.name == "bad.jpg":
            raise ValueError("Simulated OCR failure")
        return ("clean text", 1)

    monkeypatch.setattr(ps, "_run_ocr", _selective_ocr)

    result = _run(img_dir, out_dir)

    assert result["failed"] == 1
    # Two successful + one failed = three images attempted
    assert call_count["n"] == 3

    candidates = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    successful_ids = {r["screenshot_id"] for r in candidates}
    assert "good.jpg" in successful_ids
    assert "also_good.jpg" in successful_ids
    assert "bad.jpg" not in successful_ids  # failed → no output row


def test_failed_image_recorded_in_manifest(tmp_path, monkeypatch):
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["fail.jpg"])

    monkeypatch.setattr(
        ps, "_run_ocr",
        lambda reader, path: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    _run(img_dir, out_dir)

    manifest = _load_manifest(out_dir / ps._MANIFEST_FILENAME)
    entry = manifest.get("fail.jpg")
    assert entry is not None
    assert entry["status"] == "failed"
    assert entry["error"] is not None
    assert "boom" in entry["error"]


def test_failed_image_retried_on_next_run(tmp_path, monkeypatch):
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["retry.jpg"])

    # First run: OCR fails
    monkeypatch.setattr(
        ps, "_run_ocr",
        lambda reader, path: (_ for _ in ()).throw(ValueError("first failure")),
    )
    _run(img_dir, out_dir)
    manifest = _load_manifest(out_dir / ps._MANIFEST_FILENAME)
    assert manifest["retry.jpg"]["status"] == "failed"

    # Second run: OCR succeeds
    monkeypatch.setattr(ps, "_run_ocr", lambda reader, path: ("recovered text", 2))
    _run(img_dir, out_dir)

    manifest2 = _load_manifest(out_dir / ps._MANIFEST_FILENAME)
    assert manifest2["retry.jpg"]["status"] == "done"

    rows = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    assert any(r["screenshot_id"] == "retry.jpg" for r in rows)


# ---------------------------------------------------------------------------
# Incremental output
# ---------------------------------------------------------------------------

def test_csv_written_incrementally(tmp_path, monkeypatch):
    """The candidates CSV must grow after each image, not only at the end."""
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["first.jpg", "second.jpg"])

    sizes_after_each: list[int] = []

    original_append = ps._append_candidate_row

    def _capturing_append(candidates_path, row):
        original_append(candidates_path, row)
        with candidates_path.open(newline="", encoding="utf-8") as fh:
            sizes_after_each.append(sum(1 for _ in fh) - 1)  # exclude header

    monkeypatch.setattr(ps, "_append_candidate_row", _capturing_append)

    _run(img_dir, out_dir)

    # After image 1 → 1 data row; after image 2 → 2 data rows
    assert len(sizes_after_each) == 2
    assert sizes_after_each[0] == 1
    assert sizes_after_each[1] == 2


# ---------------------------------------------------------------------------
# Manifest updates
# ---------------------------------------------------------------------------

def test_manifest_flushed_at_batch_boundary(tmp_path, monkeypatch):
    """Manifest is written to disk once per completed batch, not once per image.

    With 4 images and batch_size=2 there are two batches.  _save_manifest
    must be called exactly twice — once after each batch — with 2 entries
    on disk after the first flush and 4 entries after the second flush.
    """
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["m1.jpg", "m2.jpg", "m3.jpg", "m4.jpg"])

    manifest_path = out_dir / ps._MANIFEST_FILENAME
    flush_snapshots: list[int] = []

    original_save = ps._save_manifest

    def _capturing_save(mp, manifest):
        original_save(mp, manifest)
        with manifest_path.open(encoding="utf-8") as fh:
            flush_snapshots.append(len(json.load(fh)))

    monkeypatch.setattr(ps, "_save_manifest", _capturing_save)

    _run(img_dir, out_dir, batch_size=2)

    # Two batches of 2 → exactly 2 disk flushes
    assert len(flush_snapshots) == 2, (
        f"Expected 2 batch-level flushes, got {len(flush_snapshots)}"
    )
    assert flush_snapshots[0] == 2, "After batch 1: manifest should have 2 entries"
    assert flush_snapshots[1] == 4, "After batch 2: manifest should have 4 entries"


# ---------------------------------------------------------------------------
# Preservation of manually annotated labels
# ---------------------------------------------------------------------------

def test_protected_annotation_csv_is_not_modified(tmp_path):
    """The run() function must never write to the protected annotation CSV."""
    import hashlib

    # Compute hash of the protected CSV before the run
    protected = ps._PROTECTED_CSV
    if not protected.exists():
        pytest.skip("Protected annotation CSV not present in this environment")

    before = hashlib.md5(protected.read_bytes()).hexdigest()

    img_dir = tmp_path / "imgs"
    _make_images(img_dir, ["any.jpg"])

    # Attempt to run with any valid output dir (not the data dir)
    out_dir = tmp_path / "out"
    _run(img_dir, out_dir)

    after = hashlib.md5(protected.read_bytes()).hexdigest()
    assert before == after, "Protected annotation CSV was modified"


def test_output_path_collision_with_protected_csv_raises(tmp_path):
    """run() must raise ValueError if output would overwrite the protected CSV."""
    img_dir = tmp_path / "imgs"
    _make_images(img_dir, ["x.jpg"])

    # Point output_dir to the same dir as the protected CSV so that
    # <output_dir>/candidates.csv would be the protected CSV path
    bad_output_dir = ps._PROTECTED_CSV.parent
    # Temporarily rename the candidates file name to match the protected CSV
    original_name = ps._CANDIDATES_FILENAME
    try:
        ps._CANDIDATES_FILENAME = ps._PROTECTED_CSV.name  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="protected"):
            _run(img_dir, bad_output_dir)
    finally:
        ps._CANDIDATES_FILENAME = original_name  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Separation of candidates and verified labels
# ---------------------------------------------------------------------------

def test_candidates_csv_has_auto_label_not_privacy_risk_label(tmp_path):
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["s.jpg"])

    _run(img_dir, out_dir)

    candidates = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    assert len(candidates) == 1
    row = candidates[0]

    # Must have candidate label column
    assert "auto_label_candidate" in row
    # Must NOT have verified label column (would imply it was manually verified)
    assert "privacy_risk_label" not in row


def test_auto_label_candidate_is_valid_risk_level(tmp_path):
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["img.jpg"])

    _run(img_dir, out_dir)

    rows = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    assert rows[0]["auto_label_candidate"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


# ---------------------------------------------------------------------------
# Pilot / limit flag
# ---------------------------------------------------------------------------

def test_limit_restricts_number_of_images_processed(tmp_path):
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, [f"img_{i}.jpg" for i in range(8)])

    result = _run(img_dir, out_dir, limit=3)

    assert result["total"] == 3
    candidates = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    assert len(candidates) == 3


# ---------------------------------------------------------------------------
# Empty input directory
# ---------------------------------------------------------------------------

def test_empty_input_dir_returns_zero_counts(tmp_path):
    img_dir = tmp_path / "empty_imgs"
    img_dir.mkdir()
    out_dir = tmp_path / "out"

    result = _run(img_dir, out_dir)

    assert result["processed"] == 0
    assert result["failed"] == 0
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# Non-image files are ignored
# ---------------------------------------------------------------------------

def test_non_image_files_are_ignored(tmp_path):
    img_dir = tmp_path / "mixed"
    img_dir.mkdir()
    (img_dir / "readme.txt").write_text("ignore me")
    (img_dir / "data.csv").write_text("ignore me too")
    _make_images(img_dir, ["real.jpg"])

    result = _run(img_dir, tmp_path / "out")

    assert result["total"] == 1
    candidates = _read_candidates(tmp_path / "out" / ps._CANDIDATES_FILENAME)
    assert len(candidates) == 1
    assert candidates[0]["screenshot_id"] == "real.jpg"


# ---------------------------------------------------------------------------
# OCR timeout behaviour
# ---------------------------------------------------------------------------

def test_ocr_timeout_recorded_as_failed(tmp_path, monkeypatch):
    """TimeoutError from OCR is caught, recorded as status=failed, not re-raised."""
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["timeout.jpg"])

    monkeypatch.setattr(
        ps, "_run_ocr",
        lambda reader, path: (_ for _ in ()).throw(
            TimeoutError("OCR timed out after 60s on timeout.jpg")
        ),
    )

    result = _run(img_dir, out_dir)

    assert result["failed"] == 1
    assert result["processed"] == 0

    manifest = _load_manifest(out_dir / ps._MANIFEST_FILENAME)
    entry = manifest.get("timeout.jpg")
    assert entry is not None
    assert entry["status"] == "failed"
    assert "TimeoutError" in (entry["error"] or "")


def test_subsequent_images_processed_after_timeout(tmp_path, monkeypatch):
    """A timeout on one image does not block processing of subsequent images."""
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["first.jpg", "hangs.jpg", "third.jpg"])

    def _selective(reader, path):
        if path.name == "hangs.jpg":
            raise TimeoutError("OCR timed out after 60s on hangs.jpg")
        return ("clean text", 2)

    monkeypatch.setattr(ps, "_run_ocr", _selective)

    result = _run(img_dir, out_dir)

    assert result["failed"] == 1

    manifest = _load_manifest(out_dir / ps._MANIFEST_FILENAME)
    assert manifest["hangs.jpg"]["status"] == "failed"
    assert manifest["first.jpg"]["status"] == "done"
    assert manifest["third.jpg"]["status"] == "done"

    candidates = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    ids = {r["screenshot_id"] for r in candidates}
    assert "first.jpg" in ids
    assert "third.jpg" in ids
    assert "hangs.jpg" not in ids


def test_timed_out_image_retried_on_next_run(tmp_path, monkeypatch):
    """An image that timed out (status=failed) is retried on the next run."""
    img_dir = tmp_path / "imgs"
    out_dir = tmp_path / "out"
    _make_images(img_dir, ["retry.jpg"])

    # First run: OCR times out
    monkeypatch.setattr(
        ps, "_run_ocr",
        lambda reader, path: (_ for _ in ()).throw(
            TimeoutError("OCR timed out after 60s on retry.jpg")
        ),
    )
    _run(img_dir, out_dir)

    manifest = _load_manifest(out_dir / ps._MANIFEST_FILENAME)
    assert manifest["retry.jpg"]["status"] == "failed"

    # Second run: OCR succeeds
    monkeypatch.setattr(ps, "_run_ocr", lambda reader, path: ("recovered text", 3))
    _run(img_dir, out_dir)

    manifest2 = _load_manifest(out_dir / ps._MANIFEST_FILENAME)
    assert manifest2["retry.jpg"]["status"] == "done"

    rows = _read_candidates(out_dir / ps._CANDIDATES_FILENAME)
    assert any(r["screenshot_id"] == "retry.jpg" for r in rows)

"""What the dashboard's history slider reads: where each capture sits in its stored page (moments), and the screenshots
kept for 'save' decisions."""

from __future__ import annotations

import io
import io as _io
import re
from datetime import datetime, timedelta, timezone

import pytest

from adaptation.episodes import doc_id_for
from adaptation.privacy import PassThroughFilter
from adaptation.screen_memory import run_screen_memory
from features.text_merge import SHINGLE_WORDS, AnchorLocator, TextDocument, capture_anchors
from pipeline import SnapshotPipeline
from sensing.codec import encode_message, read_message
from storage.database import connect, migrate
from storage.raw_buffer import make_context_key
from storage.screenshot_store import ScreenshotStore, encode_frame
from tests.mock_snapshot import mock_header, mock_payload
from tests.test_raw_buffer import FixedScore, XorProtector
from tests.test_screen_memory import LATER, T0, add, make_job
from tests.test_text_merge import words

DOC_ID = doc_id_for(make_context_key("chrome.exe", "medium.com", "Article"))


# --------------------------------------------------------------------------- anchors

def test_a_capture_is_found_again_after_text_is_prepended_and_appended() -> None:
    doc = TextDocument()
    doc.merge(words(40, 80))
    anchors, n = capture_anchors(words(40, 80))
    assert n == 40 and len(anchors) == 3
    doc.merge(words(10, 50))   # scrolled up: text prepended, the capture moved 30 words down
    doc.merge(words(70, 120))  # scrolled down: appended
    assert AnchorLocator(doc).locate(anchors) == (0, 30)


def test_anchors_survive_a_cut_first_word() -> None:
    doc = TextDocument()
    doc.merge(words(0, 60))
    anchors, _ = capture_anchors("xx " + words(21, 50))  # the screen edge cut the first word
    assert AnchorLocator(doc).locate(anchors) == (0, 20)


def test_short_or_unknown_text_has_no_position() -> None:
    assert capture_anchors(words(0, SHINGLE_WORDS - 1)) == ([], SHINGLE_WORDS - 1)
    doc = TextDocument()
    doc.merge(words(0, 30))
    anchors, _ = capture_anchors(words(100, 130))
    assert AnchorLocator(doc).locate(anchors) is None


def test_anchors_hold_no_words() -> None:
    anchors, _ = capture_anchors(words(0, 30))
    assert all(isinstance(o, int) and re.fullmatch(r"[0-9a-f]{16}", h) for o, h in anchors)


# --------------------------------------------------------------------------- moments in memory.db

def test_moments_point_into_the_page_and_follow_it_across_visits(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    add(job, 0, words(40, 80))
    add(job, 1, words(70, 110))
    run_screen_memory(job, now=LATER)
    first = job.memory.moments(DOC_ID)
    assert [(m["fragment"], m["start_word"], m["n_words"]) for m in first] == [(0, 0, 40), (0, 30, 31)]  # the sample text ends at word 101

    add(job, 0, words(0, 50), day=2)  # a later visit scrolls up: everything shifts by 40 words
    run_screen_memory(job, now=LATER + timedelta(days=2))
    assert [(m["fragment"], m["start_word"]) for m in job.memory.moments(DOC_ID)] == [(0, 40), (0, 70), (0, 0)]


def test_no_text_kept_means_no_moments(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=None)
    add(job, 0, words(0, 40))
    run_screen_memory(job, now=LATER)
    assert job.memory.moments(DOC_ID) == []


def test_a_refilter_moves_or_clears_the_positions(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    add(job, 0, words(0, 40))
    run_screen_memory(job, now=LATER)

    redacted = TextDocument()
    redacted.merge(words(100, 140))  # every word changed
    job.memory.replace_document_text(DOC_ID, redacted, "test")
    assert job.memory.moments(DOC_ID)[0]["fragment"] is None
    job.memory.replace_document_text(DOC_ID, None, None)
    assert job.memory.moments(DOC_ID)[0]["start_word"] is None


# --------------------------------------------------------------------------- screenshots

def frame(width: int = 64, height: int = 36) -> bytes:
    return bytes((i * 31) % 256 for i in range(width * height * 4))


def store(tmp_path, **kw) -> ScreenshotStore:
    return ScreenshotStore(tmp_path / "screenshots.db", protector=XorProtector(), **kw)


def test_frames_are_scaled_jpeg_and_not_stored_in_the_clear(tmp_path) -> None:
    from PIL import Image

    s = store(tmp_path, max_width=32)
    shot = s.add(frame(), width=64, height=36, stride=256, ts=T0, context_record_id=7)
    jpeg = s.load(shot)
    image = Image.open(_io.BytesIO(jpeg))
    assert (image.format, image.size) == ("JPEG", (32, 18))
    raw = (tmp_path / "screenshots.db").read_bytes() + (tmp_path / "screenshots.db-wal").read_bytes()
    assert jpeg[:4] not in raw  # only the protected bytes are on disk


def test_encode_frame_reads_bgra() -> None:
    from PIL import Image

    blue = bytes([255, 0, 0, 255]) * 16  # B, G, R, A
    jpeg, w, h = encode_frame(blue, 4, 4, 16)
    r, g, b = Image.open(_io.BytesIO(jpeg)).getpixel((1, 1))
    assert (w, h) == (4, 4) and b > 200 and r < 50


def test_old_frames_expire_and_the_size_cap_drops_the_oldest(tmp_path) -> None:
    s = store(tmp_path, ttl_days=10)
    for day in (0, 5, 9):
        s.add(frame(), width=64, height=36, stride=256, ts=T0 + timedelta(days=day), context_record_id=day)
    s.maintain(now=T0 + timedelta(days=12))  # day 0 is past 10 days
    assert s.stats()["shots"] == 2

    each = s.stats()["bytes"] // 2
    s.max_bytes = each  # room for one
    s.maintain(now=T0 + timedelta(days=12))
    conn = s._connection(create=False)
    assert [r[0] for r in conn.execute("SELECT context_record_id FROM screenshots")] == [9]


def snapshot(seq: int, *, title: str):
    h = mock_header(seq=seq)
    h["context"]["title"] = title
    return read_message(io.BytesIO(encode_message(h, mock_payload())))


@pytest.fixture
def conn():
    c = connect(":memory:", check_same_thread=False)
    migrate(c)
    yield c
    c.close()


def test_only_save_decisions_keep_a_screenshot(conn, tmp_path) -> None:
    s = store(tmp_path, ttl_days=36500)  # the mock frame has a fixed 2026 date; the default TTL would expire it
    SnapshotPipeline(conn, FixedScore(0.9), embed=False, screenshots=s).handle(snapshot(1, title="Kept"))
    SnapshotPipeline(conn, FixedScore(0.1), embed=False, screenshots=s).handle(snapshot(2, title="Dropped"))
    saved_id = conn.execute("SELECT id FROM context_records WHERE decision = 'save'").fetchone()[0]
    rows = s._connection(create=False).execute("SELECT context_record_id, width, height FROM screenshots").fetchall()
    assert [tuple(r) for r in rows] == [(saved_id, 4, 2)]


def test_screenshots_are_off_unless_a_store_is_given(conn, tmp_path) -> None:
    SnapshotPipeline(conn, FixedScore(0.9), embed=False).handle(snapshot(1, title="Kept"))
    assert not (tmp_path / "screenshots.db").exists()


def test_a_failing_store_does_not_lose_the_record(conn, tmp_path) -> None:
    class Broken(XorProtector):
        def protect(self, data: bytes) -> bytes:
            raise OSError("boom")

    s = ScreenshotStore(tmp_path / "screenshots.db", protector=Broken())
    SnapshotPipeline(conn, FixedScore(0.9), embed=False, screenshots=s).handle(snapshot(1, title="Kept"))
    assert conn.execute("SELECT COUNT(*) FROM context_records WHERE decision = 'save'").fetchone()[0] == 1


def test_lower_quality_makes_smaller_screenshots() -> None:
    import numpy as np

    rng = np.random.default_rng(0)
    noisy = rng.integers(0, 255, size=64 * 36 * 4, dtype=np.uint8).tobytes()
    small, _, _ = encode_frame(noisy, 64, 36, 256, quality=20)
    large, _, _ = encode_frame(noisy, 64, 36, 256, quality=90)
    assert len(small) < len(large)

"""Screen text on the wire (protocol v2), its path into the separate raw buffer, and the buffer's limits."""

from __future__ import annotations

import io
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

from models.base import ImportanceScorer
from pipeline import SnapshotPipeline
from sensing.codec import encode_message, read_message
from sensing.protocol import ProtocolError, ScreenImage, ScreenText, parse_header
from storage.database import connect, migrate
from storage.raw_buffer import DpapiProtector, RawBuffer, make_context_key
from tests.mock_snapshot import mock_header, mock_payload

T0 = datetime(2026, 10, 20, 9, 0, 0, tzinfo=timezone.utc)


class XorProtector:
    """Reversible stand-in for DPAPI, so the tests run anywhere. It also proves the stored bytes are not the text."""

    name = "xor"

    def protect(self, data: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in data)

    def unprotect(self, data: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in data)


class FixedScore(ImportanceScorer):
    name = "fixed"

    def __init__(self, p: float) -> None:
        self.p = p

    def predict_proba(self, features) -> float:
        return self.p


def text_obj(content: str = "Oxford Cotton Shirt - Slim Fit - LKR 4,990", **kw) -> dict:
    return {"source": "uia", "chars": len(content), "truncated": False, "read_ms": 38.5, "content": content, **kw}


def snapshot(seq: int = 1, *, text: dict | None = None, version: int = 2, title: str = "Shop", excluded: bool = False):
    h = mock_header(seq=seq, excluded=excluded)
    h["version"] = version
    if not excluded:
        h["context"]["title"] = title
    if text is not None:
        h["text"] = text
    return read_message(io.BytesIO(encode_message(h, mock_payload())))


def screen_text(content: str, **kw) -> ScreenText:
    return ScreenText(source="uia", chars=len(content), truncated=False, read_ms=1.0, content=content, **kw)


def buffer(tmp_path, **kw) -> RawBuffer:
    kw.setdefault("ttl_days", 36_500)  # the fixed 2026 timestamps below must not look expired
    return RawBuffer(tmp_path / "raw_buffer.db", protector=XorProtector(), **kw)


def add(buf: RawBuffer, content: str, *, minutes: int = 0, score: float | None = 0.5, title: str = "Shop", **kw) -> int | None:
    return buf.add(
        screen_text(content), ts=T0 + timedelta(minutes=minutes), app="chrome.exe", domain="shop.example.com",
        title=title, decision="save", score=score, **kw,
    )


# --------------------------------------------------------------------------- protocol v2

def test_version_2_text_is_decoded() -> None:
    snap = snapshot(text=text_obj(images=[{"x": 420, "y": 180, "width": 600, "height": 600, "alt": "Red shirt"}]))
    assert snap.text is not None
    assert snap.text.content.startswith("Oxford Cotton Shirt")
    assert snap.text.chars == len(snap.text.content)
    assert snap.text.images == (ScreenImage(420, 180, 600, 600, "Red shirt"),)


def test_version_1_still_accepted_and_has_no_text() -> None:
    assert snapshot(version=1).text is None


def test_null_or_missing_text_is_none() -> None:
    assert snapshot(text=None).text is None
    h = mock_header()
    h["version"] = 2
    h["text"] = None
    assert parse_header(h).text is None


def test_malformed_text_is_rejected() -> None:
    h = mock_header()
    h["version"] = 2
    h["text"] = {"source": "uia"}  # no content
    with pytest.raises(ProtocolError):
        parse_header(h)


def test_unknown_version_still_rejected() -> None:
    h = mock_header()
    h["version"] = 3
    with pytest.raises(ProtocolError):
        parse_header(h)


def test_text_survives_the_codec_with_the_frame_payload() -> None:
    content = "héllo wörld – 日本語"
    snap = snapshot(text=text_obj(content))
    assert snap.text.content == content
    assert snap.payload == mock_payload()


def test_a_50000_char_text_fits_the_header_limit() -> None:
    assert snapshot(text=text_obj("ä" * 50_000)).text.chars == 50_000


def test_text_content_is_not_in_the_repr() -> None:
    assert "Oxford" not in repr(snapshot(text=text_obj()))


# --------------------------------------------------------------------------- the buffer

def test_round_trip_and_text_is_not_stored_in_plain(tmp_path) -> None:
    buf = buffer(tmp_path)
    secret = "a distinctive sentence that must not be readable on disk"
    row_id = add(buf, secret, activity={"key_presses": 4, "mouse_scrolls": 9})
    buf.close()

    assert secret.encode() not in (tmp_path / "raw_buffer.db").read_bytes()
    raw = sqlite3.connect(tmp_path / "raw_buffer.db").execute("SELECT text_blob FROM raw_captures").fetchone()[0]
    assert secret.encode() not in bytes(raw)

    (capture,) = list(buffer(tmp_path).iter_unprocessed())
    assert capture.id == row_id
    assert capture.text == secret
    assert capture.ts == T0
    assert (capture.key_presses, capture.mouse_scrolls) == (4, 9)
    assert capture.decision == "save"


def test_images_and_unicode_round_trip(tmp_path) -> None:
    buf = buffer(tmp_path)
    content = "日本語 – ünïcode"
    buf.add(
        screen_text(content, images=(ScreenImage(1, 2, 300, 200, "ç"),)),
        ts=T0, app="edge", domain=None, title="t",
    )
    (capture,) = buf.iter_unprocessed()
    assert capture.text == content
    assert capture.images == ({"x": 1, "y": 2, "width": 300, "height": 200, "alt": "ç"},)


def test_empty_text_is_not_stored(tmp_path) -> None:
    buf = buffer(tmp_path)
    assert add(buf, "   \n ") is None
    assert not (tmp_path / "raw_buffer.db").exists()  # the file only appears with the first real capture


def test_long_text_is_compressed(tmp_path) -> None:
    buf = buffer(tmp_path)
    add(buf, "the same sentence again. " * 2000)  # 48,000 chars
    assert buf.stats()["text_bytes"] < 5_000


def test_ttl_deletes_old_rows_processed_or_not(tmp_path) -> None:
    buf = buffer(tmp_path, ttl_days=3)
    old = add(buf, "old", minutes=0)
    keep = add(buf, "recent", minutes=60 * 24 * 4)
    buf.mark_processed([old])
    assert buf.purge_expired(now=T0 + timedelta(days=4, minutes=1)) == 1
    assert [c.id for c in buf.iter_unprocessed()] == [keep]


def test_size_cap_drops_the_least_important_text_first_and_keeps_metadata(tmp_path) -> None:
    buf = buffer(tmp_path)
    low = add(buf, os.urandom(300).hex(), minutes=0, score=0.05)
    high = add(buf, os.urandom(300).hex(), minutes=1, score=0.95)
    mid = add(buf, os.urandom(300).hex(), minutes=2, score=0.50)
    buf.max_text_bytes = buf.stats()["text_bytes"] // 3 + 50  # room for one of the three (random text won't compress)
    buf.enforce_size_cap()

    by_id = {c.id: c for c in buf.iter_unprocessed()}
    assert by_id[low].text is None and by_id[mid].text is None  # dropped, but the rows (metadata) stay
    assert by_id[high].text is not None
    assert by_id[low].title == "Shop"
    assert buf.stats()["text_bytes"] <= buf.max_text_bytes


def test_processed_rows_are_skipped_then_deleted(tmp_path) -> None:
    buf = buffer(tmp_path)
    a, b = add(buf, "one", minutes=0), add(buf, "two", minutes=1)
    buf.mark_processed([a])
    assert [c.id for c in buf.iter_unprocessed()] == [b]
    assert buf.delete([a, b]) == 2
    assert buf.stats()["rows"] == 0


def test_reading_a_missing_buffer_creates_nothing(tmp_path) -> None:
    buf = buffer(tmp_path)
    assert list(buf.iter_unprocessed()) == []
    assert buf.stats()["rows"] == 0
    assert buf.purge_expired() == 0
    assert not (tmp_path / "raw_buffer.db").exists()


def test_context_key_ignores_dynamic_title_parts() -> None:
    a = make_context_key("Chrome.exe", "Medium.com", "(3) Understanding LightGBM and 2 more pages - Edge")
    b = make_context_key("chrome", "medium.com", "Understanding LightGBM - Edge")
    assert a == b


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_dpapi_round_trip_and_not_plaintext() -> None:
    p = DpapiProtector()
    data = b"secret screen text " * 100
    blob = p.protect(data)
    assert b"secret screen text" not in blob
    assert p.unprotect(blob) == data


def test_dpapi_is_refused_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        DpapiProtector()


# --------------------------------------------------------------------------- pipeline integration

@pytest.fixture
def conn():
    c = connect(":memory:", check_same_thread=False)
    migrate(c)
    yield c
    c.close()


def test_pipeline_buffers_text_in_the_separate_db_not_in_context_db(conn, tmp_path) -> None:
    buf = buffer(tmp_path)
    pipeline = SnapshotPipeline(conn, FixedScore(0.8), embed=False, raw_buffer=buf)
    pipeline.handle(snapshot(text=text_obj("visible page text")))

    (capture,) = buf.iter_unprocessed()
    assert capture.text == "visible page text"
    assert capture.score == pytest.approx(0.8) and capture.decision == "save"
    assert capture.context_record_id == conn.execute("SELECT id FROM context_records").fetchone()[0]
    assert "visible page text" not in "\n".join(conn.iterdump())  # context.db holds no text anywhere


def test_excluded_snapshot_is_never_buffered(conn, tmp_path) -> None:
    buf = buffer(tmp_path)
    pipeline = SnapshotPipeline(conn, FixedScore(0.8), embed=False, raw_buffer=buf)
    pipeline.handle(snapshot(excluded=True, text=text_obj("must not be kept")))
    assert buf.stats()["rows"] == 0
    assert not (tmp_path / "raw_buffer.db").exists()


def test_snapshot_without_text_or_without_a_buffer_still_stores_the_record(conn, tmp_path) -> None:
    buf = buffer(tmp_path)
    SnapshotPipeline(conn, FixedScore(0.8), embed=False, raw_buffer=buf).handle(snapshot(text=None))
    SnapshotPipeline(conn, FixedScore(0.8), embed=False).handle(snapshot(seq=2, text=text_obj("x")))
    assert buf.stats()["rows"] == 0
    assert conn.execute("SELECT COUNT(*) FROM context_records").fetchone()[0] == 2


def test_a_failing_buffer_does_not_lose_the_record_or_leak_text(conn, tmp_path, caplog) -> None:
    class Broken(XorProtector):
        def protect(self, data: bytes) -> bytes:
            raise OSError("boom with visible page text")

    buf = RawBuffer(tmp_path / "raw_buffer.db", protector=Broken())
    pipeline = SnapshotPipeline(conn, FixedScore(0.8), embed=False, raw_buffer=buf)
    pipeline.handle(snapshot(text=text_obj("visible page text")))
    assert conn.execute("SELECT COUNT(*) FROM context_records").fetchone()[0] == 1
    assert "visible page text" not in caplog.text

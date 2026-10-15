"""
Tests for backend.services.redaction — pixel-level image redaction.

Existing tests (REDACT-01 .. REDACT-12) cover the four protection actions,
degenerate bounding boxes, clamp logic, audit list accuracy, pixel-change
correctness, and original-image immutability.

Fragmentation tests (FRAG-01 .. FRAG-16) cover the sliding-window entity
mapper added to fix the case where EasyOCR splits a sensitive entity across
multiple bounding boxes.

REDACT-01  MONITOR action returns image copy with no pixel changes.
REDACT-02  BLUR_SENSITIVE blurs pixels in regions containing entity text.
REDACT-03  BLUR_SENSITIVE leaves unrelated regions unchanged.
REDACT-04  BLUR_AND_ALERT blurs all text regions.
REDACT-05  BLOCK fills entire image with black.
REDACT-06  Degenerate bounding boxes (empty / single-point) are skipped safely.
REDACT-07  Bounding boxes clamped to image boundaries.
REDACT-08  No entity matches in BLUR_SENSITIVE → no region redacted.
REDACT-09  redacted_regions audit list matches actual applied operations.
REDACT-10  Original image not mutated by redact_image().
REDACT-11  Entity substring in one box is matched exactly (non-fragmented).
REDACT-12  Entity as substring of longer OCR text is still matched.

FRAG-01  Email split at '@' — both boxes redacted.
FRAG-02  Email split at '.' between domain parts — both boxes redacted.
FRAG-03  Email split across three boxes — all three boxes redacted.
FRAG-04  URL fragment split across two boxes — both boxes redacted.
FRAG-05  Only the window containing the entity is redacted (not earlier boxes).
FRAG-06  Unrelated adjacent box is NOT redacted when entity is in a single box.
FRAG-07  Entity not found in any window of size <= _MAX_WINDOW → unmapped.
FRAG-08  entities_unmapped count reflects entities not found in any window.
FRAG-09  Whitespace-separated phone number matched via space-joined window.
FRAG-10  Two entities sharing a single OCR box — both counted as mapped.
FRAG-11  redaction_status = "redacted" when all entities mapped.
FRAG-12  redaction_status = "entities_unmapped" when none mapped.
FRAG-13  redaction_status = "partial_redaction" when some mapped, some not.
FRAG-14  BLUR_AND_ALERT reports entities_unmapped = 0.
FRAG-15  BLOCK reports entities_unmapped = 0.
FRAG-16  MONITOR reports entities_unmapped = entities_detected.
FRAG-17  match_type field is "exact" for single-box match.
FRAG-18  match_type field is "fragmented" for multi-box match.
FRAG-19  Overlapping entity values (both reference same box index) handled.
FRAG-20  compute_redaction_status covers all documented status strings.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from backend.services.redaction import (
    _bbox_to_rect,
    _find_contributing_box_indices,
    _ocr_text_contains_entity,
    compute_redaction_status,
    image_to_png_bytes,
    make_synthetic_screenshot,
    redact_image,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _solid(color=(200, 200, 200), w=200, h=100):
    return Image.new("RGB", (w, h), color)


def _arr(img):
    return np.array(img)


def _box(x1, y1, x2, y2):
    """Build an EasyOCR-style bbox [[TL],[TR],[BR],[BL]]."""
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _detection(email="alice@example.com"):
    return {
        "EMAIL":  {"matches": [email], "count": 1},
        "PHONE":  {"matches": [], "count": 0},
        "total_sensitive_entity_count": 1,
    }


def _grad():
    """Non-uniform gradient image useful for pixel-change assertions."""
    return Image.fromarray(
        np.tile(np.arange(200, dtype=np.uint8), (100, 1))[..., np.newaxis].repeat(3, axis=2)
    )


# ---------------------------------------------------------------------------
# REDACT-01  MONITOR → no pixel change
# ---------------------------------------------------------------------------

def test_redact01_monitor_no_pixel_change():
    img = _solid((150, 100, 50))
    detections = [(_box(10, 10, 80, 30), "alice@example.com", 0.99)]
    result, regions, stats = redact_image(img, detections, _detection(), "MONITOR")
    assert regions == [], "MONITOR should produce no redacted regions"
    assert _arr(result).tobytes() == _arr(img).tobytes(), "MONITOR must not change pixels"


# ---------------------------------------------------------------------------
# REDACT-02  BLUR_SENSITIVE changes pixels in the entity bbox region
# ---------------------------------------------------------------------------

def test_redact02_blur_sensitive_changes_entity_region():
    img = _solid((200, 200, 200))
    box = _box(10, 10, 90, 40)
    detections = [(box, "alice@example.com", 0.99)]
    result, regions, _ = redact_image(img, detections, _detection(), "BLUR_SENSITIVE")

    assert len(regions) == 1, "Expected one redacted region"
    assert regions[0]["action"] == "BLUR_SENSITIVE"

    # Use a gradient to confirm pixel change
    grad = _grad()
    result2, regions2, _ = redact_image(grad, detections, _detection(), "BLUR_SENSITIVE")
    arr_g  = _arr(grad)
    arr_r2 = _arr(result2)
    l, t, r, b = regions2[0]["rect"]
    assert not np.array_equal(arr_g[t:b, l:r], arr_r2[t:b, l:r]), (
        "BLUR_SENSITIVE must change pixels in entity region for non-uniform input"
    )


# ---------------------------------------------------------------------------
# REDACT-03  BLUR_SENSITIVE leaves non-entity text regions unchanged
# ---------------------------------------------------------------------------

def test_redact03_blur_sensitive_leaves_other_regions_unchanged():
    grad = _grad()
    entity_box  = _box(10, 10, 80, 40)
    benign_box  = _box(100, 60, 190, 90)
    detections = [
        (entity_box,  "alice@example.com", 0.99),
        (benign_box,  "ordinary text",     0.99),
    ]
    result, regions, _ = redact_image(grad, detections, _detection(), "BLUR_SENSITIVE")

    assert len(regions) == 1, f"Expected 1 region, got {len(regions)}"

    arr_orig   = _arr(grad)
    arr_result = _arr(result)
    l, t, r, b = _bbox_to_rect(benign_box, *grad.size)
    assert np.array_equal(arr_orig[t:b, l:r], arr_result[t:b, l:r]), (
        "Non-entity region must not be changed by BLUR_SENSITIVE"
    )


# ---------------------------------------------------------------------------
# REDACT-04  BLUR_AND_ALERT blurs all text regions
# ---------------------------------------------------------------------------

def test_redact04_blur_and_alert_blurs_all_text_regions():
    grad = _grad()
    detections = [
        (_box(10, 10, 80, 40),   "alice@example.com", 0.99),
        (_box(100, 60, 190, 90), "ordinary text",     0.99),
    ]
    result, regions, stats = redact_image(grad, detections, _detection(), "BLUR_AND_ALERT")
    assert len(regions) == 2, "BLUR_AND_ALERT must redact all text regions"
    for reg in regions:
        assert reg["action"] == "BLUR_AND_ALERT"
        assert reg["reason"] == "all-text"
    assert stats["entities_unmapped"] == 0


# ---------------------------------------------------------------------------
# REDACT-05  BLOCK fills entire image with black
# ---------------------------------------------------------------------------

def test_redact05_block_fills_image_black():
    img = _solid((255, 100, 50))
    result, regions, stats = redact_image(img, [], _detection(), "BLOCK")
    assert len(regions) == 1
    assert regions[0]["action"] == "BLOCK"
    arr = _arr(result)
    assert arr.max() == 0, "BLOCK must produce an entirely black image"
    assert stats["entities_unmapped"] == 0


# ---------------------------------------------------------------------------
# REDACT-06  Degenerate bboxes skipped safely
# ---------------------------------------------------------------------------

def test_redact06_empty_bbox_skipped():
    img = _solid()
    detections = [([], "alice@example.com", 0.99)]  # empty bbox
    result, regions, _ = redact_image(img, detections, _detection(), "BLUR_SENSITIVE")
    assert regions == [], "Empty bbox must be skipped without error"


def test_redact06_single_point_bbox_skipped():
    img = _solid()
    detections = [([[50, 50]], "alice@example.com", 0.99)]  # single point
    result, regions, _ = redact_image(img, detections, _detection(), "BLUR_SENSITIVE")
    assert isinstance(regions, list)


# ---------------------------------------------------------------------------
# REDACT-07  Bounding boxes clamped to image boundaries
# ---------------------------------------------------------------------------

def test_redact07_bbox_clamped_to_image():
    w, h = 100, 80
    bbox = [[-20, -10], [200, -10], [200, 200], [-20, 200]]
    rect = _bbox_to_rect(bbox, w, h)
    l, t, r, b = rect
    assert l >= 0 and t >= 0
    assert r <= w and b <= h


# ---------------------------------------------------------------------------
# REDACT-08  No entity matches → no BLUR_SENSITIVE regions
# ---------------------------------------------------------------------------

def test_redact08_no_entity_no_redaction():
    img = _solid()
    detections = [(_box(10, 10, 90, 40), "hello world", 0.99)]
    detection_output = {
        "EMAIL": {"matches": [], "count": 0},
        "total_sensitive_entity_count": 0,
    }
    result, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")
    assert regions == [], "No entity matches must produce no BLUR_SENSITIVE regions"
    assert stats["entities_detected"] == 0


# ---------------------------------------------------------------------------
# REDACT-09  redacted_regions audit list accuracy
# ---------------------------------------------------------------------------

def test_redact09_audit_list_contains_correct_reason():
    img = _solid()
    detections = [(_box(5, 5, 50, 30), "alice@example.com", 0.99)]
    _, regions, _ = redact_image(img, detections, _detection(), "BLUR_SENSITIVE")
    assert regions[0]["reason"] == "sensitive-entity"
    _, regions2, _ = redact_image(img, detections, _detection(), "BLUR_AND_ALERT")
    assert regions2[0]["reason"] == "all-text"


# ---------------------------------------------------------------------------
# REDACT-10  Original image not mutated
# ---------------------------------------------------------------------------

def test_redact10_original_not_mutated():
    img = _solid((180, 180, 180))
    orig_bytes = _arr(img).tobytes()
    detections = [(_box(10, 10, 90, 40), "alice@example.com", 0.99)]
    redact_image(img, detections, _detection(), "BLUR_SENSITIVE")
    assert _arr(img).tobytes() == orig_bytes, "redact_image must not mutate the source image"


# ---------------------------------------------------------------------------
# REDACT-11  Entity in a single OCR box → exact match (not fragmented)
# ---------------------------------------------------------------------------

def test_redact11_exact_single_box_match():
    img = _solid()
    detections = [(_box(5, 5, 150, 30), "alice@example.com", 0.99)]
    _, regions, stats = redact_image(img, detections, _detection(), "BLUR_SENSITIVE")
    assert len(regions) == 1, "Single-box match must produce one region"
    assert regions[0]["match_type"] == "exact"
    assert stats["entities_mapped"] == 1
    assert stats["fragmented_matches"] == 0


# ---------------------------------------------------------------------------
# REDACT-12  Entity as substring of longer OCR text is still matched
# ---------------------------------------------------------------------------

def test_redact12_partial_substring_does_match():
    img = _solid()
    detections = [(_box(5, 5, 150, 30), "Contact alice@example.com for help", 0.95)]
    _, regions, stats = redact_image(img, detections, _detection(), "BLUR_SENSITIVE")
    assert len(regions) == 1, "Entity as substring of OCR text must be matched"
    assert stats["entities_mapped"] == 1


# ---------------------------------------------------------------------------
# FRAG-01  Email split at '@' — both boxes redacted
# ---------------------------------------------------------------------------

def test_frag01_email_split_at_at_sign():
    """Both boxes that together spell 'alice@example.com' must be redacted."""
    img = _solid()
    entity = "alice@example.com"
    detections = [
        (_box(10, 10, 55, 30), "alice@",      0.95),
        (_box(56, 10, 120, 30), "example.com", 0.95),
    ]
    detection_output = {"EMAIL": {"matches": [entity], "count": 1},
                        "total_sensitive_entity_count": 1}

    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")

    assert len(regions) == 2, (
        "Email split at '@' must produce 2 redacted regions (one per fragment box)"
    )
    assert stats["entities_mapped"] == 1
    assert stats["entities_unmapped"] == 0
    assert stats["fragmented_matches"] == 1


# ---------------------------------------------------------------------------
# FRAG-02  Email split at '.' between domain parts
# ---------------------------------------------------------------------------

def test_frag02_email_split_at_domain_dot():
    """'alice@example' + '.com' must both be redacted."""
    img = _solid()
    entity = "alice@example.com"
    detections = [
        (_box(10, 10, 80, 30), "alice@example", 0.95),
        (_box(81, 10, 120, 30), ".com",          0.95),
    ]
    detection_output = {"EMAIL": {"matches": [entity], "count": 1},
                        "total_sensitive_entity_count": 1}

    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")

    assert len(regions) == 2, "Email split at domain dot must redact both fragment boxes"
    assert stats["fragmented_matches"] == 1


# ---------------------------------------------------------------------------
# FRAG-03  Email split across three boxes
# ---------------------------------------------------------------------------

def test_frag03_email_split_three_boxes():
    """'alice' + '@example' + '.com' must all three be redacted."""
    img = _solid()
    entity = "alice@example.com"
    detections = [
        (_box(5,  10, 40, 30), "alice",    0.90),
        (_box(41, 10, 80, 30), "@example", 0.90),
        (_box(81, 10, 115, 30), ".com",    0.90),
    ]
    detection_output = {"EMAIL": {"matches": [entity], "count": 1},
                        "total_sensitive_entity_count": 1}

    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")

    assert len(regions) == 3, "3-part email split must redact all three boxes"
    assert stats["fragmented_matches"] == 1
    assert stats["entities_mapped"] == 1


# ---------------------------------------------------------------------------
# FRAG-04  URL fragment split across two boxes
# ---------------------------------------------------------------------------

def test_frag04_url_split_two_boxes():
    """'https://example' + '.com/page' (URL fragment) must both be redacted."""
    img = _solid()
    entity = "https://example.com/page"
    detections = [
        (_box(5,  10, 70, 30), "https://example", 0.90),
        (_box(71, 10, 130, 30), ".com/page",      0.90),
    ]
    detection_output = {"URL": {"matches": [entity], "count": 1},
                        "total_sensitive_entity_count": 1}

    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")

    assert len(regions) == 2, "URL split across two boxes must redact both"
    assert stats["fragmented_matches"] == 1


# ---------------------------------------------------------------------------
# FRAG-05  Only the window containing the entity is redacted (not earlier boxes)
# ---------------------------------------------------------------------------

def test_frag05_earlier_unrelated_box_not_redacted():
    """
    Given: [unrelated text] [fragment-1] [fragment-2]
    The entity is in boxes 1+2 only.  Box 0 must not be redacted.
    """
    img = _solid()
    entity = "bob@work.org"
    detections = [
        (_box(0,  10, 50, 30), "unrelated stuff",  0.99),  # index 0 — not part of entity
        (_box(51, 10, 90, 30), "bob@work",          0.95),  # index 1 — fragment 1
        (_box(91, 10, 130, 30), ".org",             0.95),  # index 2 — fragment 2
    ]
    detection_output = {"EMAIL": {"matches": [entity], "count": 1},
                        "total_sensitive_entity_count": 1}

    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")

    # Exactly 2 regions: boxes 1 and 2
    assert len(regions) == 2, (
        f"Expected 2 regions (boxes 1+2 only), got {len(regions)}"
    )
    rects = [tuple(r["rect"]) for r in regions]
    box0_rect = _bbox_to_rect(_box(0, 10, 50, 30), *img.size)
    assert tuple(box0_rect) not in rects, "Unrelated earlier box must NOT be redacted"


# ---------------------------------------------------------------------------
# FRAG-06  Unrelated adjacent box NOT redacted when entity fits one box
# ---------------------------------------------------------------------------

def test_frag06_adjacent_box_not_redacted_when_exact_match():
    """When the entity fits in a single box, adjacent boxes must not be touched."""
    img = _solid()
    entity = "alice@example.com"
    detections = [
        (_box(0,  10, 50,  30), "adjacent text",    0.99),
        (_box(51, 10, 160, 30), "alice@example.com", 0.95),
        (_box(161, 10, 190, 30), "more text",        0.99),
    ]
    detection_output = {"EMAIL": {"matches": [entity], "count": 1},
                        "total_sensitive_entity_count": 1}

    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")

    assert len(regions) == 1, "Exact match must produce exactly 1 region"
    assert regions[0]["match_type"] == "exact"


# ---------------------------------------------------------------------------
# FRAG-07  Entity entirely absent from all windows → unmapped
# ---------------------------------------------------------------------------

def test_frag07_entity_absent_from_all_windows_is_unmapped():
    """Entity 'never@found.io' does not appear in any box or window."""
    img = _solid()
    entity = "never@found.io"
    detections = [
        (_box(10, 10, 80, 30), "hello world", 0.99),
        (_box(81, 10, 150, 30), "foo bar",    0.99),
    ]
    detection_output = {"EMAIL": {"matches": [entity], "count": 1},
                        "total_sensitive_entity_count": 1}

    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")

    assert regions == [], "Entity absent from all windows must produce no redacted regions"
    assert stats["entities_unmapped"] == 1
    assert stats["entities_mapped"] == 0
    assert "EMAIL" in stats["unmapped_entity_categories"]


# ---------------------------------------------------------------------------
# FRAG-08  entities_unmapped count reflects how many could not be mapped
# ---------------------------------------------------------------------------

def test_frag08_unmapped_count_is_accurate():
    """Two entities, one found, one not → entities_unmapped == 1."""
    img = _solid()
    detections = [
        (_box(10, 10, 120, 30), "alice@example.com", 0.99),  # found
    ]
    detection_output = {
        "EMAIL": {"matches": ["alice@example.com", "ghost@nowhere.xyz"], "count": 2},
        "total_sensitive_entity_count": 2,
    }

    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")

    assert stats["entities_detected"] == 2
    assert stats["entities_mapped"] == 1
    assert stats["entities_unmapped"] == 1


# ---------------------------------------------------------------------------
# FRAG-09  Whitespace-separated phone matched via space-joined window
# ---------------------------------------------------------------------------

def test_frag09_phone_number_space_separated():
    """Phone '555 123 4567' split as '555' + '123' + '4567' across 3 boxes."""
    img = _solid()
    entity = "555 123 4567"
    detections = [
        (_box(0,  10, 30, 30), "555",  0.90),
        (_box(31, 10, 60, 30), "123",  0.90),
        (_box(61, 10, 95, 30), "4567", 0.90),
    ]
    detection_output = {"PHONE": {"matches": [entity], "count": 1},
                        "total_sensitive_entity_count": 1}

    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")

    assert len(regions) == 3, "Space-separated phone must redact all 3 digit boxes"
    assert stats["fragmented_matches"] == 1


# ---------------------------------------------------------------------------
# FRAG-10  Two entities sharing a single OCR box
# ---------------------------------------------------------------------------

def test_frag10_two_entities_same_box():
    """Both 'alice@example.com' and 'alice' appear in the same box."""
    img = _solid()
    detections = [(_box(5, 5, 150, 30), "alice@example.com and alice", 0.99)]
    detection_output = {
        "EMAIL": {"matches": ["alice@example.com"], "count": 1},
        "PHONE": {"matches": [], "count": 0},
        "total_sensitive_entity_count": 1,
    }

    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")

    assert len(regions) == 1, "Both entities map to same box — one region"
    assert stats["entities_mapped"] == 1


# ---------------------------------------------------------------------------
# FRAG-11  redaction_status = "redacted" when all entities mapped
# ---------------------------------------------------------------------------

def test_frag11_status_redacted_when_all_mapped():
    _, _, stats = redact_image(
        _solid(),
        [(_box(5, 5, 150, 30), "alice@example.com", 0.99)],
        _detection(),
        "BLUR_SENSITIVE",
    )
    status = compute_redaction_status(
        "BLUR_SENSITIVE",
        stats["entities_detected"],
        stats["entities_mapped"],
        stats["entities_unmapped"],
    )
    assert status == "redacted"


# ---------------------------------------------------------------------------
# FRAG-12  redaction_status = "entities_unmapped" when none mapped
# ---------------------------------------------------------------------------

def test_frag12_status_entities_unmapped():
    _, _, stats = redact_image(
        _solid(),
        [(_box(5, 5, 80, 30), "nothing sensitive", 0.99)],
        _detection("ghost@nowhere.xyz"),
        "BLUR_SENSITIVE",
    )
    status = compute_redaction_status(
        "BLUR_SENSITIVE",
        stats["entities_detected"],
        stats["entities_mapped"],
        stats["entities_unmapped"],
    )
    assert status == "entities_unmapped"


# ---------------------------------------------------------------------------
# FRAG-13  redaction_status = "partial_redaction" when some mapped, some not
# ---------------------------------------------------------------------------

def test_frag13_status_partial_redaction():
    img = _solid()
    detections = [(_box(5, 5, 150, 30), "alice@example.com", 0.99)]
    detection_output = {
        "EMAIL": {"matches": ["alice@example.com", "ghost@nowhere.xyz"], "count": 2},
        "total_sensitive_entity_count": 2,
    }
    _, _, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")
    status = compute_redaction_status(
        "BLUR_SENSITIVE",
        stats["entities_detected"],
        stats["entities_mapped"],
        stats["entities_unmapped"],
    )
    assert status == "partial_redaction"


# ---------------------------------------------------------------------------
# FRAG-14  BLUR_AND_ALERT stats — entities_unmapped always 0
# ---------------------------------------------------------------------------

def test_frag14_blur_and_alert_stats_all_covered():
    detections = [(_box(5, 5, 80, 30), "any text", 0.99)]
    _, _, stats = redact_image(_solid(), detections, _detection(), "BLUR_AND_ALERT")
    assert stats["entities_unmapped"] == 0
    assert stats["entities_mapped"] == stats["entities_detected"]


# ---------------------------------------------------------------------------
# FRAG-15  BLOCK stats — entities_unmapped always 0
# ---------------------------------------------------------------------------

def test_frag15_block_stats_all_covered():
    _, _, stats = redact_image(_solid(), [], _detection(), "BLOCK")
    assert stats["entities_unmapped"] == 0
    assert stats["entities_mapped"] == stats["entities_detected"]


# ---------------------------------------------------------------------------
# FRAG-16  MONITOR stats — entities_unmapped == entities_detected
# ---------------------------------------------------------------------------

def test_frag16_monitor_stats_none_mapped():
    _, _, stats = redact_image(_solid(), [], _detection(), "MONITOR")
    assert stats["entities_mapped"] == 0
    assert stats["entities_unmapped"] == stats["entities_detected"]


# ---------------------------------------------------------------------------
# FRAG-17  match_type "exact" for single-box match
# ---------------------------------------------------------------------------

def test_frag17_match_type_exact():
    detections = [(_box(5, 5, 150, 30), "alice@example.com", 0.99)]
    _, regions, _ = redact_image(_solid(), detections, _detection(), "BLUR_SENSITIVE")
    assert regions[0]["match_type"] == "exact"


# ---------------------------------------------------------------------------
# FRAG-18  match_type "fragmented" for multi-box match
# ---------------------------------------------------------------------------

def test_frag18_match_type_fragmented():
    entity = "alice@example.com"
    detections = [
        (_box(10, 10, 55, 30), "alice@",      0.95),
        (_box(56, 10, 120, 30), "example.com", 0.95),
    ]
    detection_output = {"EMAIL": {"matches": [entity], "count": 1},
                        "total_sensitive_entity_count": 1}
    _, regions, _ = redact_image(_solid(), detections, detection_output, "BLUR_SENSITIVE")
    assert all(r["match_type"] == "fragmented" for r in regions)


# ---------------------------------------------------------------------------
# FRAG-19  Overlapping entity values share a box — handled without crash
# ---------------------------------------------------------------------------

def test_frag19_overlapping_entities_same_box():
    """
    Two entities that both map to the same box index are handled cleanly.
    The box is redacted once; stats report 2 mapped, 0 unmapped.
    """
    img = _solid()
    detections = [(_box(5, 5, 150, 30), "alice@example.com 192.168.1.1", 0.99)]
    detection_output = {
        "EMAIL":      {"matches": ["alice@example.com"], "count": 1},
        "IP_ADDRESS": {"matches": ["192.168.1.1"],       "count": 1},
        "total_sensitive_entity_count": 2,
    }
    _, regions, stats = redact_image(img, detections, detection_output, "BLUR_SENSITIVE")
    assert len(regions) == 1, "One box contains both entities — one region"
    assert stats["entities_mapped"] == 2
    assert stats["entities_unmapped"] == 0


# ---------------------------------------------------------------------------
# FRAG-20  compute_redaction_status covers all documented status strings
# ---------------------------------------------------------------------------

def test_frag20_compute_redaction_status_all_values():
    cases = [
        ("MONITOR",       0, 0, 0, "no_entities_detected"),
        ("MONITOR",       2, 0, 2, "monitor"),
        ("BLOCK",         1, 1, 0, "block"),
        ("BLUR_AND_ALERT", 3, 3, 0, "redacted_all_text"),
        ("BLUR_SENSITIVE", 2, 2, 0, "redacted"),
        ("BLUR_SENSITIVE", 3, 1, 2, "partial_redaction"),
        ("BLUR_SENSITIVE", 2, 0, 2, "entities_unmapped"),
    ]
    for action, detected, mapped, unmapped, expected in cases:
        result = compute_redaction_status(action, detected, mapped, unmapped)
        assert result == expected, (
            f"compute_redaction_status({action!r}, {detected}, {mapped}, {unmapped}) "
            f"expected {expected!r}, got {result!r}"
        )


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------

def test_image_to_png_bytes_returns_valid_png():
    data = image_to_png_bytes(_solid())
    assert isinstance(data, bytes)
    assert data[:4] == b"\x89PNG", "Expected PNG magic bytes"


def test_make_synthetic_screenshot_dimensions():
    img = make_synthetic_screenshot(300, 150)
    assert img.size == (300, 150)


def test_ocr_text_contains_entity_case_insensitive():
    assert _ocr_text_contains_entity("Alice@Example.COM is here", ["alice@example.com"])
    assert not _ocr_text_contains_entity("nothing sensitive", ["alice@example.com"])


# ---------------------------------------------------------------------------
# _find_contributing_box_indices direct tests
# ---------------------------------------------------------------------------

def test_find_contributing_exact_match():
    detections = [
        (_box(0,  0, 50, 20), "hello world",       0.9),
        (_box(51, 0, 130, 20), "alice@example.com", 0.9),
    ]
    result = _find_contributing_box_indices(["alice@example.com"], detections)
    assert result["alice@example.com"] == {1}


def test_find_contributing_fragmented_window2():
    detections = [
        (_box(0,  0, 50, 20), "alice@", 0.9),
        (_box(51, 0, 120, 20), "example.com", 0.9),
    ]
    result = _find_contributing_box_indices(["alice@example.com"], detections)
    assert result["alice@example.com"] == {0, 1}


def test_find_contributing_no_match_returns_empty_set():
    detections = [
        (_box(0, 0, 50, 20), "totally unrelated", 0.9),
    ]
    result = _find_contributing_box_indices(["ghost@nowhere.io"], detections)
    assert result["ghost@nowhere.io"] == set()


def test_find_contributing_empty_entity_returns_empty_set():
    detections = [(_box(0, 0, 50, 20), "any text", 0.9)]
    result = _find_contributing_box_indices([""], detections)
    assert result[""] == set()


def test_find_contributing_empty_detections_returns_empty_set():
    result = _find_contributing_box_indices(["alice@example.com"], [])
    assert result["alice@example.com"] == set()

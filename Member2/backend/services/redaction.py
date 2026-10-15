"""
Pixel-level image redaction for CAPRLM.

Maps OCR bounding boxes to detected sensitive entity strings and applies
actual Gaussian blur or opaque masking to those image regions.

Protection actions:
  MONITOR         - no pixel change; original returned
  BLUR_SENSITIVE  - blur only regions whose text contains a sensitive entity
  BLUR_AND_ALERT  - blur every text region (all OCR bboxes)
  BLOCK           - fill entire image with opaque black

The original image is never modified; a copy is always returned.
Bounding boxes are clamped to image dimensions; degenerate boxes are skipped.

Fragmentation handling (BLUR_SENSITIVE only)
--------------------------------------------
EasyOCR may split a single sensitive entity across multiple consecutive
bounding boxes (e.g. "alice@" in one box, "example.com" in the next).
When an entity value is not found as a substring of any single OCR text box,
a sliding-window search over windows of 2 and 3 consecutive detections is
attempted.  Within each window the texts are concatenated with no separator
(handles tight splits like "alice@" + "example.com") and with a single space
(handles spaced patterns like "555" + "123" + "4567").  The first, smallest
window that produces a match is used; every box in that window is redacted.

This maximises recall for fragmented entities at the bounded cost of
potentially including an adjacent box that frames the entity.  It is not a
false-positive-free guarantee.  Entities that cannot be found in any window
of up to _MAX_WINDOW boxes are recorded as unmapped.

Return value change
-------------------
redact_image() now returns a 3-tuple:
  (redacted_image, redacted_regions, redaction_stats)

redaction_stats keys:
  entities_detected          total entity instances in detection_output
  entities_mapped            entities for which at least one box was found
  entities_unmapped          entities not mappable to any OCR region
  unmapped_entity_categories categories with at least one unmapped entity
  fragmented_matches         count of entities that required multi-box windows
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageDraw

# Gaussian blur radius applied to redacted regions.
_BLUR_RADIUS = 15

# Minimum entity string length to attempt fragmented window matching.
# Short values (< 4 chars) are too ambiguous across concatenated boxes.
_MIN_ENTITY_LEN_FOR_FRAGMENTED = 4

# Maximum number of consecutive boxes to join in one window.
_MAX_WINDOW = 3

BBox = list[list[float]]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] from EasyOCR


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _bbox_to_rect(bbox: BBox, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """Convert an EasyOCR polygon to a clamped PIL crop rectangle."""
    xs = [float(pt[0]) for pt in bbox]
    ys = [float(pt[1]) for pt in bbox]
    left   = max(0, int(min(xs)))
    top    = max(0, int(min(ys)))
    right  = min(img_w, int(max(xs)) + 1)
    bottom = min(img_h, int(max(ys)) + 1)
    return left, top, right, bottom


# ---------------------------------------------------------------------------
# Entity extraction helpers
# ---------------------------------------------------------------------------

def _all_entity_values(detection_output: dict[str, Any]) -> list[str]:
    """Return every matched entity string from sensitive_detection output."""
    values: list[str] = []
    for cat, info in detection_output.items():
        if cat == "total_sensitive_entity_count":
            continue
        if isinstance(info, dict):
            values.extend(str(v) for v in info.get("matches", []))
    return values


def _ocr_text_contains_entity(ocr_text: str, entity_values: list[str]) -> bool:
    """Return True if any entity value is a case-insensitive substring of ocr_text."""
    lo = ocr_text.lower()
    return any(v.lower() in lo for v in entity_values if v)


def _entity_category(entity_val: str, detection_output: dict[str, Any]) -> str | None:
    """Return the first detection category that claims this entity value."""
    for cat, info in detection_output.items():
        if cat == "total_sensitive_entity_count":
            continue
        if isinstance(info, dict) and entity_val in info.get("matches", []):
            return cat
    return None


# ---------------------------------------------------------------------------
# Fragmentation-aware entity-to-box mapping
# ---------------------------------------------------------------------------

def _find_contributing_box_indices(
    entity_values: list[str],
    ocr_detections: list[tuple[BBox, str, float]],
    max_window: int = _MAX_WINDOW,
) -> dict[str, set[int]]:
    """
    Map each entity value to the set of OCR detection indices that contain it.

    Algorithm
    ---------
    Pass 1 — single-box substring match (existing behaviour, O(N·E)).
    Pass 2 — if pass 1 fails and len(entity) >= _MIN_ENTITY_LEN_FOR_FRAGMENTED,
              slide windows of size 2 .. max_window over consecutive detections.
              For each window: concatenate texts with no separator and with a
              single space, then check whether the entity (lowercase) is a
              substring of either joined string.  The first (smallest, earliest)
              window that matches is used, limiting over-redaction.

    Parameters
    ----------
    entity_values:
        Entity strings from _all_entity_values(detection_output).
    ocr_detections:
        Ordered (bbox, text, confidence) list from EasyOCR.
    max_window:
        Maximum consecutive-box window size for fragmented matching.

    Returns
    -------
    dict mapping each entity string to a set of detection indices.
    An empty set means the entity could not be mapped to any OCR region.
    """
    n = len(ocr_detections)
    texts = [d[1] for d in ocr_detections]

    result: dict[str, set[int]] = {}

    for entity in entity_values:
        if not entity:
            result[entity] = set()
            continue

        entity_lower = entity.lower().strip()
        found: set[int] | None = None

        # Pass 1: single-box substring match
        for i, text in enumerate(texts):
            if entity_lower in text.lower():
                found = {i}
                break

        # Pass 2: sliding window over 2..max_window consecutive boxes
        if found is None and len(entity) >= _MIN_ENTITY_LEN_FOR_FRAGMENTED and n >= 2:
            outer_done = False
            for window_size in range(2, min(max_window + 1, n + 1)):
                if outer_done:
                    break
                for start in range(n - window_size + 1):
                    window_texts = texts[start: start + window_size]
                    # No-separator: "alice@" + "example.com" → "alice@example.com"
                    joined_no_sep = "".join(window_texts).lower()
                    # Single space: "555" + "123" + "4567" → "555 123 4567"
                    joined_space  = " ".join(window_texts).lower()

                    if entity_lower in joined_no_sep or entity_lower in joined_space:
                        found = set(range(start, start + window_size))
                        outer_done = True
                        break

        result[entity] = found if found is not None else set()

    return result


# ---------------------------------------------------------------------------
# Redaction-status computation
# ---------------------------------------------------------------------------

def compute_redaction_status(
    protection_action: str,
    entities_detected: int,
    entities_mapped: int,
    entities_unmapped: int,
) -> str:
    """
    Return a single string describing the redaction outcome.

    Values
    ------
    "no_entities_detected"  — NER found zero sensitive entities
    "monitor"               — MONITOR action; no redaction attempted
    "block"                 — BLOCK applied; full image blacked out
    "redacted_all_text"     — BLUR_AND_ALERT; all OCR text regions blurred
    "redacted"              — BLUR_SENSITIVE; all entities mapped and blurred
    "partial_redaction"     — BLUR_SENSITIVE; some mapped, some unmapped
    "entities_unmapped"     — BLUR_SENSITIVE; entities present, none mapped
    """
    if entities_detected == 0:
        return "no_entities_detected"
    if protection_action == "MONITOR":
        return "monitor"
    if protection_action == "BLOCK":
        return "block"
    if protection_action == "BLUR_AND_ALERT":
        return "redacted_all_text"
    # BLUR_SENSITIVE
    if entities_unmapped == 0 and entities_mapped > 0:
        return "redacted"
    if entities_mapped > 0 and entities_unmapped > 0:
        return "partial_redaction"
    return "entities_unmapped"


# ---------------------------------------------------------------------------
# Main redaction entry point
# ---------------------------------------------------------------------------

def redact_image(
    image: Image.Image,
    ocr_detections: list[tuple[BBox, str, float]],
    detection_output: dict[str, Any],
    protection_action: str,
) -> tuple[Image.Image, list[dict[str, Any]], dict[str, Any]]:
    """
    Apply pixel-level redaction to a PIL Image.

    Parameters
    ----------
    image:
        Source PIL Image.  Never modified; a copy is always returned.
    ocr_detections:
        List of (bbox, text, confidence) from EasyOCR / extract_text_with_boxes().
    detection_output:
        Output of detect_sensitive_entities() — provides entity match strings.
    protection_action:
        'MONITOR' | 'BLUR_SENSITIVE' | 'BLUR_AND_ALERT' | 'BLOCK'

    Returns
    -------
    (redacted_image, redacted_regions, redaction_stats)
        redacted_image   : PIL Image (RGB) with sensitive regions transformed
        redacted_regions : list of audit dicts
                           {rect, action, reason, match_type}
                           match_type is one of "exact", "fragmented",
                           "all-text", or "block"
        redaction_stats  : dict with keys:
                           entities_detected, entities_mapped,
                           entities_unmapped, unmapped_entity_categories,
                           fragmented_matches
    """
    entity_values = _all_entity_values(detection_output)
    n_entities    = len(entity_values)

    # ------------------------------------------------------------------
    # MONITOR — return copy with no changes
    # ------------------------------------------------------------------
    if protection_action == "MONITOR":
        stats: dict[str, Any] = {
            "entities_detected":           n_entities,
            "entities_mapped":             0,
            "entities_unmapped":           n_entities,
            "unmapped_entity_categories":  [],
            "fragmented_matches":          0,
        }
        return image.copy(), [], stats

    result   = image.copy().convert("RGB")
    img_w, img_h = result.size
    redacted_regions: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # BLOCK — fill entire image with opaque black
    # ------------------------------------------------------------------
    if protection_action == "BLOCK":
        draw = ImageDraw.Draw(result)
        draw.rectangle([0, 0, img_w, img_h], fill=(0, 0, 0))
        redacted_regions.append({
            "rect":       [0, 0, img_w, img_h],
            "action":     "BLOCK",
            "reason":     "risk=CRITICAL",
            "match_type": "block",
        })
        stats = {
            "entities_detected":           n_entities,
            "entities_mapped":             n_entities,
            "entities_unmapped":           0,
            "unmapped_entity_categories":  [],
            "fragmented_matches":          0,
        }
        return result, redacted_regions, stats

    # ------------------------------------------------------------------
    # BLUR_AND_ALERT — blur every OCR text region unconditionally
    # ------------------------------------------------------------------
    if protection_action == "BLUR_AND_ALERT":
        for bbox, _text, _conf in ocr_detections:
            if not bbox or len(bbox) < 2:
                continue
            rect = _bbox_to_rect(bbox, img_w, img_h)
            left, top, right, bottom = rect
            if right <= left or bottom <= top:
                continue
            region  = result.crop(rect)
            blurred = region.filter(ImageFilter.GaussianBlur(radius=_BLUR_RADIUS))
            result.paste(blurred, (left, top))
            redacted_regions.append({
                "rect":       list(rect),
                "action":     "BLUR_AND_ALERT",
                "reason":     "all-text",
                "match_type": "all-text",
            })
        stats = {
            "entities_detected":           n_entities,
            "entities_mapped":             n_entities,
            "entities_unmapped":           0,
            "unmapped_entity_categories":  [],
            "fragmented_matches":          0,
        }
        return result, redacted_regions, stats

    # ------------------------------------------------------------------
    # BLUR_SENSITIVE — entity-directed blurring with fragmentation support
    # ------------------------------------------------------------------
    contributing = _find_contributing_box_indices(entity_values, ocr_detections)

    # Build a map from detection index → match type for blurring
    index_to_match_type: dict[int, str] = {}
    entities_mapped    = 0
    entities_unmapped  = 0
    unmapped_cats: list[str] = []
    fragmented_count   = 0

    for entity_val, indices in contributing.items():
        if not indices:
            entities_unmapped += 1
            cat = _entity_category(entity_val, detection_output)
            if cat and cat not in unmapped_cats:
                unmapped_cats.append(cat)
        else:
            entities_mapped += 1
            mtype = "fragmented" if len(indices) > 1 else "exact"
            if mtype == "fragmented":
                fragmented_count += 1
            for idx in indices:
                # Prefer "fragmented" label if any entity needs multi-box coverage
                if idx not in index_to_match_type or index_to_match_type[idx] == "exact":
                    index_to_match_type[idx] = mtype

    # Apply blur to every index that has a match
    for i, (bbox, _text, _conf) in enumerate(ocr_detections):
        if i not in index_to_match_type:
            continue
        if not bbox or len(bbox) < 2:
            continue
        rect = _bbox_to_rect(bbox, img_w, img_h)
        left, top, right, bottom = rect
        if right <= left or bottom <= top:
            continue
        region  = result.crop(rect)
        blurred = region.filter(ImageFilter.GaussianBlur(radius=_BLUR_RADIUS))
        result.paste(blurred, (left, top))
        redacted_regions.append({
            "rect":       list(rect),
            "action":     "BLUR_SENSITIVE",
            "reason":     "sensitive-entity",
            "match_type": index_to_match_type[i],
        })

    stats = {
        "entities_detected":           n_entities,
        "entities_mapped":             entities_mapped,
        "entities_unmapped":           entities_unmapped,
        "unmapped_entity_categories":  unmapped_cats,
        "fragmented_matches":          fragmented_count,
    }
    return result, redacted_regions, stats


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def image_to_png_bytes(image: Image.Image) -> bytes:
    """Encode a PIL Image to PNG bytes (for HTTP response or storage)."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def load_image(path: str | Path) -> Image.Image:
    """Load an image from disk, raising ValueError for invalid paths."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Image not found: {p}")
    return Image.open(p)


def make_synthetic_screenshot(
    width:    int = 400,
    height:   int = 200,
    bg_color: tuple[int, int, int] = (240, 240, 240),
) -> Image.Image:
    """
    Create a synthetic solid-colour image for testing.

    Tests use this to verify that pixel values actually change in redacted
    regions without needing real screenshots.
    """
    return Image.new("RGB", (width, height), bg_color)

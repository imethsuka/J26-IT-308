"""
Visual feature extraction for CAPRLM.

Derives spatial and layout features from EasyOCR bounding boxes without
requiring a trained vision model.  All features use only the bboxes
returned by extract_text_with_boxes() and the PIL image dimensions.

Features produced:
  text_density_ratio        fraction of image area covered by text boxes
  text_box_count            total OCR detection boxes
  sensitive_box_count       boxes whose text contains a sensitive entity
  sensitive_area_ratio      fraction of image area covered by sensitive boxes
  entity_cluster_score      0.0 = evenly spread, 1.0 = tightly clustered
  quadrant_sensitive_counts how many sensitive boxes fall in each screen quadrant

These are returned in the API response as `visual_features` and document
which screen regions contain sensitive information.  They are not yet fed
into the Random Forest classifier (which would require annotation-corrected
retraining); they are computed and returned for audit and future use.
"""

from __future__ import annotations

from typing import Any

BBox = list[list[float]]


def _bbox_area(bbox: BBox, img_w: int, img_h: int) -> int:
    """Return the pixel area of a bounding-box rectangle, clamped to image size."""
    if not bbox or len(bbox) < 2:
        return 0
    xs = [float(p[0]) for p in bbox]
    ys = [float(p[1]) for p in bbox]
    w = min(img_w, max(xs)) - max(0, min(xs))
    h = min(img_h, max(ys)) - max(0, min(ys))
    return max(0, int(w * h))


def _bbox_center(bbox: BBox) -> tuple[float, float]:
    xs = [float(p[0]) for p in bbox]
    ys = [float(p[1]) for p in bbox]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _ocr_text_contains_entity(ocr_text: str, entity_values: list[str]) -> bool:
    lo = ocr_text.lower()
    return any(v.lower() in lo for v in entity_values if v)


def _cluster_score(centers: list[tuple[float, float]], img_w: int, img_h: int) -> float:
    """
    Simple clustering coefficient in [0, 1].

    Computed as 1 - (mean pairwise normalised distance).
    Returns 0.0 if fewer than 2 centres (undefined).
    """
    if len(centers) < 2:
        return 0.0
    norm_w = float(img_w) or 1.0
    norm_h = float(img_h) or 1.0
    distances: list[float] = []
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            dx = (centers[i][0] - centers[j][0]) / norm_w
            dy = (centers[i][1] - centers[j][1]) / norm_h
            distances.append((dx ** 2 + dy ** 2) ** 0.5)
    mean_dist = sum(distances) / len(distances)
    # max possible normalised distance on a unit rectangle is sqrt(2) ≈ 1.414
    return round(max(0.0, 1.0 - mean_dist / 1.414), 4)


def extract_visual_features(
    img_w: int,
    img_h: int,
    ocr_detections: list[tuple[BBox, str, float]],
    entity_values: list[str],
) -> dict[str, Any]:
    """
    Compute layout-based visual features.

    Parameters
    ----------
    img_w, img_h:
        Image dimensions in pixels.
    ocr_detections:
        List of (bbox, text, confidence) from extract_text_with_boxes().
    entity_values:
        All matched entity strings (from _all_entity_values or ner_service).

    Returns
    -------
    dict with all visual feature keys documented at module level.
    """
    if img_w <= 0 or img_h <= 0:
        return _empty_features()

    total_img_area = img_w * img_h
    total_text_area = 0
    sensitive_area = 0
    sensitive_centers: list[tuple[float, float]] = []
    sensitive_box_count = 0

    quadrant_counts = {"Q1_top_left": 0, "Q2_top_right": 0, "Q3_bottom_left": 0, "Q4_bottom_right": 0}
    half_w = img_w / 2
    half_h = img_h / 2

    for bbox, text, _conf in ocr_detections:
        if not bbox:
            continue
        area = _bbox_area(bbox, img_w, img_h)
        total_text_area += area

        if _ocr_text_contains_entity(text, entity_values):
            sensitive_area += area
            sensitive_box_count += 1
            cx, cy = _bbox_center(bbox)
            sensitive_centers.append((cx, cy))
            q = ("Q1_top_left" if cx < half_w and cy < half_h else
                 "Q2_top_right" if cx >= half_w and cy < half_h else
                 "Q3_bottom_left" if cx < half_w else "Q4_bottom_right")
            quadrant_counts[q] += 1

    text_density = round(total_text_area / total_img_area, 4) if total_img_area else 0.0
    sensitive_area_ratio = round(sensitive_area / total_img_area, 4) if total_img_area else 0.0
    cluster_score = _cluster_score(sensitive_centers, img_w, img_h)

    return {
        "text_density_ratio": text_density,
        "text_box_count": len(ocr_detections),
        "sensitive_box_count": sensitive_box_count,
        "sensitive_area_ratio": sensitive_area_ratio,
        "entity_cluster_score": cluster_score,
        "quadrant_sensitive_counts": quadrant_counts,
        "image_dimensions": {"width": img_w, "height": img_h},
        "cv_method": "ocr_bbox_layout_analysis",
    }


def _empty_features() -> dict[str, Any]:
    return {
        "text_density_ratio": 0.0,
        "text_box_count": 0,
        "sensitive_box_count": 0,
        "sensitive_area_ratio": 0.0,
        "entity_cluster_score": 0.0,
        "quadrant_sensitive_counts": {"Q1_top_left": 0, "Q2_top_right": 0, "Q3_bottom_left": 0, "Q4_bottom_right": 0},
        "image_dimensions": {"width": 0, "height": 0},
        "cv_method": "ocr_bbox_layout_analysis",
    }

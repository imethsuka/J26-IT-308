"""
Tests for backend.services.cv_features — visual feature extraction.

CV-01  text_density_ratio is in [0, 1].
CV-02  text_box_count equals len(ocr_detections).
CV-03  sensitive_box_count only counts boxes containing entity text.
CV-04  sensitive_area_ratio is in [0, 1].
CV-05  entity_cluster_score is 0.0 when fewer than 2 sensitive boxes.
CV-06  entity_cluster_score is in [0, 1] for multiple boxes.
CV-07  quadrant_sensitive_counts keys match expected quadrant names.
CV-08  quadrant counts sum to sensitive_box_count.
CV-09  Zero image dimensions return empty features without error.
CV-10  cv_method field present.
CV-11  No sensitive entities → sensitive_box_count == 0.
"""

from __future__ import annotations

import pytest

from backend.services.cv_features import extract_visual_features

_QUADRANTS = {"Q1_top_left", "Q2_top_right", "Q3_bottom_left", "Q4_bottom_right"}


def _box(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _det(bbox, text, conf=0.9):
    return (bbox, text, conf)


# ---------------------------------------------------------------------------
# CV-01  text_density_ratio in [0, 1]
# ---------------------------------------------------------------------------

def test_cv01_text_density_in_range():
    ocr = [_det(_box(0, 0, 50, 50), "hello")]
    f = extract_visual_features(100, 100, ocr, [])
    assert 0.0 <= f["text_density_ratio"] <= 1.0


# ---------------------------------------------------------------------------
# CV-02  text_box_count equals len(ocr_detections)
# ---------------------------------------------------------------------------

def test_cv02_text_box_count_correct():
    ocr = [_det(_box(0, 0, 10, 10), "a"), _det(_box(20, 20, 30, 30), "b")]
    f = extract_visual_features(100, 100, ocr, [])
    assert f["text_box_count"] == 2


# ---------------------------------------------------------------------------
# CV-03  sensitive_box_count correct
# ---------------------------------------------------------------------------

def test_cv03_sensitive_box_count_correct():
    entity_vals = ["alice@example.com"]
    ocr = [
        _det(_box(0, 0, 50, 30), "alice@example.com"),
        _det(_box(60, 0, 100, 30), "ordinary text"),
    ]
    f = extract_visual_features(200, 100, ocr, entity_vals)
    assert f["sensitive_box_count"] == 1


# ---------------------------------------------------------------------------
# CV-04  sensitive_area_ratio in [0, 1]
# ---------------------------------------------------------------------------

def test_cv04_sensitive_area_ratio_in_range():
    ocr = [_det(_box(0, 0, 50, 50), "alice@example.com")]
    f = extract_visual_features(100, 100, ocr, ["alice@example.com"])
    assert 0.0 <= f["sensitive_area_ratio"] <= 1.0


# ---------------------------------------------------------------------------
# CV-05  cluster_score = 0.0 for fewer than 2 sensitive boxes
# ---------------------------------------------------------------------------

def test_cv05_cluster_score_zero_single_box():
    ocr = [_det(_box(10, 10, 40, 30), "alice@example.com")]
    f = extract_visual_features(100, 100, ocr, ["alice@example.com"])
    assert f["entity_cluster_score"] == 0.0


# ---------------------------------------------------------------------------
# CV-06  cluster_score in [0, 1] for multiple boxes
# ---------------------------------------------------------------------------

def test_cv06_cluster_score_two_boxes_in_range():
    ocr = [
        _det(_box(0, 0, 20, 20),   "alice@example.com"),
        _det(_box(80, 80, 100, 100), "bob@example.com"),
    ]
    f = extract_visual_features(200, 200, ocr, ["alice@example.com", "bob@example.com"])
    assert 0.0 <= f["entity_cluster_score"] <= 1.0


def test_cv06_cluster_score_adjacent_boxes_higher_than_spread():
    # Adjacent boxes should have higher cluster score than spread boxes
    ocr_close = [
        _det(_box(10, 10, 30, 30), "alice@example.com"),
        _det(_box(32, 10, 52, 30), "bob@example.com"),
    ]
    ocr_spread = [
        _det(_box(0, 0, 20, 20),     "alice@example.com"),
        _det(_box(180, 180, 200, 200), "bob@example.com"),
    ]
    entity_vals = ["alice@example.com", "bob@example.com"]
    f_close  = extract_visual_features(200, 200, ocr_close,  entity_vals)
    f_spread = extract_visual_features(200, 200, ocr_spread, entity_vals)
    assert f_close["entity_cluster_score"] >= f_spread["entity_cluster_score"]


# ---------------------------------------------------------------------------
# CV-07  quadrant keys correct
# ---------------------------------------------------------------------------

def test_cv07_quadrant_keys_correct():
    f = extract_visual_features(100, 100, [], [])
    assert set(f["quadrant_sensitive_counts"].keys()) == _QUADRANTS


# ---------------------------------------------------------------------------
# CV-08  quadrant counts sum to sensitive_box_count
# ---------------------------------------------------------------------------

def test_cv08_quadrant_counts_sum_to_sensitive():
    entity_vals = ["alice@example.com", "bob@test.org"]
    ocr = [
        _det(_box(0,  0,  20, 20), "alice@example.com"),  # Q1
        _det(_box(60, 0,  80, 20), "bob@test.org"),        # Q2
        _det(_box(0,  60, 20, 80), "benign"),              # Q3 (not entity)
    ]
    f = extract_visual_features(100, 100, ocr, entity_vals)
    quad_sum = sum(f["quadrant_sensitive_counts"].values())
    assert quad_sum == f["sensitive_box_count"]


# ---------------------------------------------------------------------------
# CV-09  Zero dimensions → empty features, no error
# ---------------------------------------------------------------------------

def test_cv09_zero_dimensions_safe():
    f = extract_visual_features(0, 0, [], [])
    assert f["text_density_ratio"] == 0.0
    assert f["sensitive_box_count"] == 0


# ---------------------------------------------------------------------------
# CV-10  cv_method field
# ---------------------------------------------------------------------------

def test_cv10_cv_method_field():
    f = extract_visual_features(100, 100, [], [])
    assert f.get("cv_method") == "ocr_bbox_layout_analysis"


# ---------------------------------------------------------------------------
# CV-11  No entities → sensitive_box_count == 0
# ---------------------------------------------------------------------------

def test_cv11_no_entities_no_sensitive_boxes():
    ocr = [_det(_box(0, 0, 50, 50), "hello world")]
    f = extract_visual_features(100, 100, ocr, [])
    assert f["sensitive_box_count"] == 0
    assert f["sensitive_area_ratio"] == 0.0

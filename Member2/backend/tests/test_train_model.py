"""Unit tests for train_model feature engineering and split correctness."""

import pandas as pd
import pytest
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split

from backend.services.train_model import build_features, ENTITY_FEATURES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(ctx: str, label: str, entity_val: int = 0) -> dict:
    return {
        "screenshot_id": f"s_{ctx}",
        "application_context": ctx,
        **{col: entity_val for col in ENTITY_FEATURES},
        "privacy_risk_label": label,
    }


def _df(*rows) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# build_features — encoder fitting
# ---------------------------------------------------------------------------

def test_build_features_fits_encoder_on_provided_df():
    df = _df(
        _make_row("AppA", "LOW"),
        _make_row("AppB", "MEDIUM"),
        _make_row("AppC", "HIGH"),
    )
    X, enc = build_features(df)
    assert isinstance(enc, LabelEncoder)
    assert set(enc.classes_) == {"AppA", "AppB", "AppC"}
    assert "application_context_encoded" in X.columns
    assert len(X) == 3


def test_build_features_reuses_provided_encoder():
    df_train = _df(_make_row("AppA", "LOW"), _make_row("AppB", "LOW"))
    _, enc = build_features(df_train)

    df_test = _df(_make_row("AppA", "LOW"), _make_row("AppC", "LOW"))
    X_test, enc2 = build_features(df_test, context_encoder=enc)

    # Same encoder object is returned unchanged
    assert enc2 is enc
    encoded = X_test["application_context_encoded"].tolist()
    # AppA is known → ≥ 0; AppC is unseen → -1
    assert encoded[0] >= 0
    assert encoded[1] == -1


def test_encoder_classes_do_not_include_test_only_categories():
    """
    After a proper train/test split the encoder must not contain categories
    that exist only in the test fold — verified end-to-end.
    """
    rows = (
        [_make_row("AppA", "LOW")] * 3
        + [_make_row("AppB", "LOW")] * 3
        + [_make_row("AppE", "MEDIUM")] * 2
        + [_make_row("AppF", "MEDIUM")] * 2
    )
    df = pd.DataFrame(rows)
    y = df["privacy_risk_label"].values

    df_train, df_test, _, _ = train_test_split(
        df, y, test_size=0.30, random_state=42, stratify=y
    )

    _, enc = build_features(df_train)
    train_contexts = set(df_train["application_context"].unique())
    test_only = set(df_test["application_context"].unique()) - train_contexts

    # If any context appeared only in the test fold, the encoder must not know it
    for ctx in test_only:
        assert ctx not in enc.classes_, (
            f"Encoder was fitted on test-only category '{ctx}' — leakage present."
        )


def test_feature_matrix_has_expected_columns():
    df = _df(_make_row("VS Code", "LOW"))
    X, _ = build_features(df)
    expected = set(ENTITY_FEATURES) | {"application_context_encoded"}
    assert set(X.columns) == expected


def test_entity_columns_are_integer():
    df = _df(_make_row("AppA", "LOW", entity_val=1))
    X, _ = build_features(df)
    for col in ENTITY_FEATURES:
        assert X[col].dtype in (int, "int64", "int32"), f"{col} is not integer"

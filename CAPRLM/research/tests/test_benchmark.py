from research.data.placeholder_scenarios import generate
from research.eval.benchmark import (ABLATIONS, BASELINES, FULL, make_folds, paired_difference,
                                     run_benchmark, summarize, summarize_categories)
from research.model.features import ALL_GROUPS, featurize
from research.detection import Detector
from shared.types import PrivacyContext


def test_random_folds_partition_the_data_without_overlap():
    sc = generate(50, 1, test=False)
    folds = list(make_folds(sc, 5, seed=3))
    assert len(folds) == 5
    tests = [t.text for _, _, tst in folds for t in tst]
    assert len(tests) == len(set(tests)) == len({s.text for s in sc})
    for _, train, test in folds:
        assert not {s.text for s in train} & {s.text for s in test}


def test_kind_split_holds_out_one_whole_context_kind():
    sc = generate(120, 2, test=False)
    folds = list(make_folds(sc, 5, 1, split="kind", min_kind_rows=1))
    assert len(folds) == len({s.kind for s in sc})
    for kind, train, test in folds:
        assert {s.kind for s in test} == {kind}
        assert kind not in {s.kind for s in train}


def test_kind_split_skips_kinds_too_small_to_score():
    sc = generate(120, 2, test=False)
    sizes = {k: sum(s.kind == k for s in sc) for k in {s.kind for s in sc}}
    kept = {kind for kind, _, _ in make_folds(sc, 5, 1, split="kind", min_kind_rows=15)}
    assert kept == {k for k, n in sizes.items() if n >= 15}


def test_feature_groups_only_emit_their_own_keys():
    d = Detector(ner=None)
    text = "Your password is: hunter22 for the portal"
    cand = [c for c in d.detect(text) if c.category == "CREDENTIAL"][0]
    ctx = PrivacyContext(app="chrome", domain="secure.examplebank.com", title="Account Summary")
    base = featurize(cand, ctx, ("base",))
    assert base and all(k.startswith(("cat=", "confidence", "det=")) and "|" not in k for k in base)
    full = featurize(cand, ctx, ALL_GROUPS)
    assert set(base) < set(full)
    assert not any(k.startswith(("app=", "dom=", "title=", "appb=", "cat=CREDENTIAL|ctx="))
                   for k in featurize(cand, ctx, ("base", "window", "shape", "doc")))


def test_benchmark_runs_and_reports_every_filter_per_fold():
    sc = generate(60, 5, test=False)
    rows, cats = run_benchmark(sc, Detector(ner=None), k=3, seeds=(1,), tune=False, progress=lambda *_: None)
    names = {r["name"] for r in rows}
    assert names >= set(ABLATIONS) | set(BASELINES) | {"CAPRLM: full, untuned"}
    assert len(rows) == 3 * len(names)
    table = {s["name"]: s for s in summarize(rows)}
    assert all(0.0 <= table[n]["f1"] <= 1.0 for n in table)
    d = paired_difference(rows, FULL, "static_masking")
    assert d["n"] == 3 and d["mean_diff"] is not None
    assert summarize_categories(cats)

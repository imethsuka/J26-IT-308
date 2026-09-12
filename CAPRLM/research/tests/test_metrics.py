from shared.types import FilterResult, PrivacyContext

from research.data.placeholder_scenarios import GoldSpan, Scenario
from research.eval.metrics import evaluate_filter, identifying_tokens

CTX = PrivacyContext(app="chrome")


class _Fixed:
    def __init__(self, fn):
        self.fn = fn

    def filter(self, text, ctx):
        return FilterResult(text=self.fn(text))


def _scenarios():
    return [
        Scenario("mail nimal.perera@example.com about Acme", CTX, "email",
                 (GoldSpan("nimal.perera@example.com", "EMAIL", "high"), GoldSpan("Acme", "ORG", "none"))),
        Scenario("Kavindi Fernando lives here", CTX, "email", (GoldSpan("Kavindi Fernando", "PERSON", "medium"),)),
    ]


def test_perfect_filter():
    f = _Fixed(lambda t: t.replace("nimal.perera@example.com", "[E]").replace("Kavindi Fernando", "[P]"))
    m = evaluate_filter(f, _scenarios())
    assert (m["precision"], m["recall"], m["f1"]) == (1.0, 1.0, 1.0)
    assert m["leakage_rate"] == 0.0 and m["reidentification_rate"] == 0.0


def test_passthrough_leaks_everything():
    m = evaluate_filter(_Fixed(lambda t: t), _scenarios())
    assert m["recall"] == 0.0 and m["leakage_rate"] == 1.0 and m["reidentification_rate"] == 1.0
    assert abs(m["semantic_similarity"] - 1.0) < 1e-9


def test_over_redaction_costs_precision_not_recall():
    f = _Fixed(lambda t: t.replace("nimal.perera@example.com", "[E]").replace("Kavindi Fernando", "[P]").replace("Acme", "[O]"))
    m = evaluate_filter(f, _scenarios())
    assert m["recall"] == 1.0 and abs(m["precision"] - 2 / 3) < 1e-9


def test_partial_leak_is_caught_by_reidentification_but_not_exact_leakage():
    f = _Fixed(lambda t: t.replace("Kavindi Fernando", "Fernando").replace("nimal.perera@example.com", "[E]"))
    m = evaluate_filter(f, _scenarios())
    assert m["leakage_rate"] == 0.0           # the full name no longer appears verbatim
    assert m["reidentification_rate"] == 0.5  # but its surname does


def test_exception_is_scored_as_blocked():
    def boom(_):
        raise RuntimeError("x")
    m = evaluate_filter(_Fixed(boom), _scenarios())
    assert m["errors"] == 2 and m["blocked_rate"] == 1.0 and m["leakage_rate"] == 0.0
    assert m["semantic_similarity"] == 0.0


def test_email_identifying_tokens_ignore_domain():
    assert identifying_tokens("nimal.perera@example.com", "EMAIL") == {"nimal", "perera"}

import pytest

from shared.privacy_filter import ImageFilter, PrivacyFilter
from shared.types import PrivacyContext, Source

from research.baselines import PseudonymizationFilter, SessionKeyStore
from research.caprlm_filter import CAPRLMFilter
from research.detection import Detector, OcrUnavailableError

CTX = PrivacyContext(app="chrome", domain="secure.examplebank.com", title="Account Summary")
TEXT = "Hi Sarah Johnson, call 077-123-4567 or write to sarah.johnson@example.com. Balance: $4,500."


class _BrokenModel:
    version = "caprlm/broken"

    def predict(self, pairs):
        raise RuntimeError("boom")


class _FakeOcr:
    def __init__(self, text=None, error=None):
        self.text, self.error = text, error

    def image_to_text(self, image):
        if self.error:
            raise self.error
        return self.text


def test_all_filters_satisfy_the_protocol(filters):
    assert all(isinstance(f, PrivacyFilter) for f in filters.values())
    assert isinstance(filters["caprlm"], ImageFilter)


def test_caprlm_redacts_and_audits_without_raw_content(filters):
    res = filters["caprlm"].filter(TEXT, CTX)
    assert not res.blocked and res.redactions > 0
    assert "077-123-4567" not in res.text and "sarah.johnson@example.com" not in res.text
    assert {r.category for r in res.audit} >= {"PHONE", "EMAIL"}
    assert res.filter_version.startswith("caprlm/")


def test_caprlm_fails_closed_when_model_raises(detector):
    res = CAPRLMFilter(_BrokenModel(), detector).filter(TEXT, CTX)
    assert res.blocked and res.text == ""


def test_caprlm_fails_closed_when_ner_model_missing(model):
    from research.detection import SpacyNerDetector
    broken = Detector(SpacyNerDetector("no_such_model_installed"))
    res = CAPRLMFilter(model, broken).filter(TEXT, CTX)
    assert res.blocked and res.text == ""


def test_filter_image_runs_ocr_then_filters_as_ocr_source(model, detector):
    f = CAPRLMFilter(model, Detector(detector.ner, ocr=_FakeOcr("call 077-123-4567 now")))
    res = f.filter_image(b"img", CTX)
    assert not res.blocked and "077-123-4567" not in res.text


def test_filter_image_fails_closed_when_ocr_unavailable(model, detector):
    f = CAPRLMFilter(model, Detector(detector.ner, ocr=_FakeOcr(error=OcrUnavailableError("no tesseract"))))
    res = f.filter_image(b"img", CTX)
    assert res.blocked and res.text == ""


def test_ctx_source_is_forced_to_ocr_for_images(model, detector):
    seen = []
    real = model.predict
    model.predict = lambda pairs: (seen.extend(x.source for _, x in pairs), real(pairs))[1]
    try:
        CAPRLMFilter(model, Detector(detector.ner, ocr=_FakeOcr("mail a@b.com"))).filter_image(
            b"img", PrivacyContext(app="x", source=Source.UIA))
    finally:
        del model.predict
    assert seen and set(seen) == {Source.OCR}


def test_same_context_different_tier(filters):
    """The point of the model: one entity type, different handling by context."""
    f = filters["caprlm"]
    text = "Forward it to sarah.johnson@example.com please."
    inbox = f.filter(text, PrivacyContext(app="outlook", title="Inbox - Outlook"))
    bank = f.filter(text, PrivacyContext(app="chrome", domain="secure.examplebank.com", title="Account Summary"))
    assert inbox.audit[0].risk_tier != bank.audit[0].risk_tier


def test_static_masking_and_anonymization_outputs(filters):
    assert "****@****" in filters["static_masking"].filter(TEXT, CTX).text
    anon = filters["anonymization"].filter(TEXT, CTX).text
    assert "[EMAIL]" in anon and "[PHONE]" in anon


def test_pseudonymization_is_consistent_and_reversible_only_with_key_store(detector):
    store = SessionKeyStore()
    f = PseudonymizationFilter(detector, store)
    a = f.filter("write to sarah@example.com", CTX).text
    b = f.filter("again: sarah@example.com, cc tom@example.com", CTX).text
    sur = a.removeprefix("write to ")
    assert sur in b                                   # same email -> same surrogate
    assert "sarah@example.com" not in a + b
    assert store.reveal(a) == "write to sarah@example.com"
    assert len(store) == 2


@pytest.mark.parametrize("name", ["caprlm", "static_masking", "anonymization", "pseudonymization"])
def test_empty_text(name, filters):
    res = filters[name].filter("", CTX)
    assert res.text == "" and not res.blocked and res.redactions == 0

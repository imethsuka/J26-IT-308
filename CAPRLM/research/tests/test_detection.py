from research.detection import Detector, OcrUnavailableError, TesseractOcr
from research.detection.regex_detector import RegexDetector, luhn_valid

TEXT = "Contact john.doe@example.com or call 077-123-4567 about card 4111 1111 1111 1111."


def _cats(cands, text):
    return {c.surface(text): c.category for c in cands}


def test_regex_finds_structured_entities():
    got = _cats(RegexDetector().detect(TEXT), TEXT)
    assert got["john.doe@example.com"] == "EMAIL"
    assert got["077-123-4567"] == "PHONE"
    assert got["4111 1111 1111 1111"] == "CARD_LIKE_NUMBER"


def test_luhn_valid_card_beats_overlapping_phone_match():
    cands = RegexDetector().detect("pay 4111 1111 1111 1111 now")
    assert [c.category for c in cands] == ["CARD_LIKE_NUMBER"]
    assert cands[0].confidence > 0.9


def test_email_span_excludes_sentence_final_period():
    text = "write to sarah@example.com."
    (c,) = RegexDetector().detect(text)
    assert c.surface(text) == "sarah@example.com"


def test_luhn():
    assert luhn_valid("4111 1111 1111 1111")
    assert not luhn_valid("4111 1111 1111 1112")
    assert not luhn_valid("1234")


def test_credential_pattern_swallows_embedded_email():
    text = "password: hunter2@mail.com"
    cands = RegexDetector().detect(text)
    assert [(c.category, c.surface(text)) for c in cands] == [("CREDENTIAL", text)]


def test_ner_adds_entities_regex_cannot_see(detector):
    text = "Sarah Johnson moved to Berlin and emailed sarah@example.com."
    got = _cats(detector.detect(text), text)
    assert got["sarah@example.com"] == "EMAIL"
    assert got["Berlin"] == "LOCATION"
    assert "Sarah Johnson" in got


def test_regex_wins_overlap_with_ner(detector):
    cands = detector.detect("Write to sarah.johnson@example.com")
    spans = [(c.start, c.end) for c in cands]
    assert all(not (a[0] < b[1] and b[0] < a[1]) for i, a in enumerate(spans) for b in spans[i + 1:])


def test_regex_only_mode_is_explicit():
    d = Detector(ner=None)
    assert {c.detector for c in d.detect("Sarah Johnson, sarah@example.com")} == {"regex"}


def test_tesseract_missing_raises_ocr_unavailable(monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", r"C:\definitely\not\tesseract.exe")
    try:
        TesseractOcr().image_to_text(b"not an image")
    except OcrUnavailableError:
        return
    raise AssertionError("expected OcrUnavailableError")

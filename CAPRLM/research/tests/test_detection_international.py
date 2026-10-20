import pytest

from research.detection import Detector
from research.detection.regex_detector import RegexDetector, iban_valid, ipv4_valid

rx = RegexDetector()


def _found(text):
    return {c.surface(text): c.category for c in rx.detect(text)}


@pytest.mark.parametrize("text,surface,category", [
    ("NIC 912345678V on file", "912345678V", "NATIONAL_ID"),
    ("SSN 123-45-6789", "123-45-6789", "NATIONAL_ID"),
    ("NI number QQ 12 34 56 C", "QQ 12 34 56 C", "NATIONAL_ID"),
    ("NRIC S1234567D", "S1234567D", "NATIONAL_ID"),
    ("CPF 123.456.789-09", "123.456.789-09", "NATIONAL_ID"),
    ("IBAN DE89370400440532013000 ok", "DE89370400440532013000", "BANK_ACCOUNT"),
    ("IBAN ES91 2100 0418 4502 0005 1332", "ES91 2100 0418 4502 0005 1332", "BANK_ACCOUNT"),
    ("IFSC HDFC0001234", "HDFC0001234", "BANK_ACCOUNT"),
    ("SWIFT COBADEFFXXX", "COBADEFFXXX", "BANK_ACCOUNT"),
    ("from 192.168.1.45 today", "192.168.1.45", "IP_ADDRESS"),
    ("due 22.10.2026", "22.10.2026", "DATE"),
    ("due 2026年10月30日", "2026年10月30日", "DATE"),
    ("total EUR 1.250,00 paid", "EUR 1.250,00", "FINANCIAL_FIGURE"),
    ("total LKR 1,250,000.00", "LKR 1,250,000.00", "FINANCIAL_FIGURE"),
    ("call 0 7 7 1 2 3 4 5 6 7", "0 7 7 1 2 3 4 5 6 7", "PHONE"),
    ("mail john dot doe at gmail dot com", "john dot doe at gmail dot com", "EMAIL"),
    ("link https://x.test/a?session=abc123def", "https://x.test/a?session=abc123def", "URL_TOKEN"),
    ("takes metformin 500 mg daily", "metformin 500 mg", "MEDICAL"),
    ("key AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE", "CREDENTIAL"),
])
def test_international_formats(text, surface, category):
    assert _found(text).get(surface) == category


def test_checksums_reject_near_misses():
    assert iban_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_valid("DE89370400440532013001")
    assert ipv4_valid("10.0.0.55") and not ipv4_valid("999.1.1.1")
    assert "DE89370400440532013001" not in _found("IBAN DE89370400440532013001")
    assert "999.1.1.1" not in _found("ip 999.1.1.1")


def test_version_number_is_not_a_date_or_ip():
    got = _found("build 4.8.2 released")
    assert "4.8.2" not in got


def test_swift_rejects_ordinary_uppercase_words():
    assert "PASSWORD" not in _found("enter PASSWORD below")


def test_candidates_carry_surrounding_words_but_not_the_span():
    d = Detector(ner=None)
    text = "Your password is: hunter22 for the portal"
    (c,) = [c for c in d.detect(text) if c.category == "CREDENTIAL"]
    assert any(w.startswith("L:") for w in c.window) or any(w.startswith("R:") for w in c.window)
    assert not any("hunter" in w for w in c.window)

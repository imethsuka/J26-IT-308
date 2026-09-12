"""Rule-based detection: the three categories already in shared/rule_based_filter.py (reused,
not copied) plus structured international formats — dates, amounts, national IDs, bank
identifiers (IBAN/SWIFT/IFSC), IP addresses, URL tokens, credentials, medical identifiers.

Rules are deliberately recall-oriented where a format is ambiguous (a 12-digit number may be a
Sri Lankan NIC or an order id): the detector's job is to propose candidates, and the risk
model decides how sensitive each is *in context*. Confidence reflects how specific the
pattern is, and is a feature for that model. Where a checksum exists (Luhn, IBAN mod-97) it
is used to raise confidence or reject.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from shared.rule_based_filter import _PATTERNS as _BASELINE_PATTERNS

from .candidate import Candidate


def luhn_valid(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def iban_valid(s: str) -> bool:
    s = re.sub(r"\s", "", s).upper()
    if not 15 <= len(s) <= 34 or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    moved = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1
    except ValueError:
        return False


def ipv4_valid(s: str) -> bool:
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts)


# Country codes accepted as the 5th-6th letters of a SWIFT/BIC (rejects ordinary 8-letter words).
_COUNTRIES = set(
    "LK US GB IN DE FR AU SG AE CA BR ZA NG PK BD JP CN CH ES IT NL BE SE NO DK FI IE PT AT PL "
    "RU TR SA QA KW OM BH EG KE GH MY ID TH VN PH KR HK NZ MX AR CL CO PE".split()
)


def swift_valid(s: str) -> bool:
    return s[4:6] in _COUNTRIES


@dataclass(frozen=True)
class Rule:
    category: str
    pattern: re.Pattern[str]
    confidence: float
    group: int = 0                                   # which regex group is the sensitive span
    validator: Callable[[str], bool] | None = None   # reject a match when it returns False
    boosted: tuple[Callable[[str], bool], float] | None = None  # (check, confidence if it holds)


def _r(p: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(p, flags)


_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_CUR = r"(?:LKR|USD|EUR|GBP|JPY|INR|AUD|CAD|SGD|AED|PKR|BDT|CNY|CHF|ZAR|NGN|BRL|SAR|MYR|Rs\.?)"
_AMOUNT = r"\d{1,3}(?:[.,\s]\d{2,3})*(?:[.,]\d{1,2})?|\d+"

_MEDICAL_TERMS = (
    # conditions
    "diabetes|diabetic|hypertension|asthma|an(?:a)?emia|migraine|hypothyroidism|hyperthyroidism|cancer|"
    "arthritis|depression|anxiety disorder|epilepsy|hiv|tuberculosis|covid(?:-19)?|pneumonia|cholesterol|"
    "kidney disease|heart disease|copd|eczema|psoriasis|pregnan(?:t|cy)|hepatitis|dengue|malaria|stroke|"
    "bronchitis|allerg(?:y|ies)|insomnia|bipolar|schizophrenia|adhd|autism|obesity|thalassemia|"
    "sickle cell|leuk(?:a)?emia|lymphoma|fracture|chemotherapy|dialysis|"
    # medications
    "metformin|insulin|levothyroxine|atorvastatin|amlodipine|lisinopril|losartan|omeprazole|"
    "paracetamol|ibuprofen|amoxicillin|azithromycin|prednisolone|salbutamol|sertraline|fluoxetine|"
    "warfarin|aspirin|ferrous sulfate|atenolol|metoprolol|simvastatin|gabapentin|tramadol|morphine|"
    "diazepam|alprazolam"
)
_DOSE = r"(?:\s+\d+(?:\.\d+)?\s?(?:mg|mcg|µg|ml|g|iu)\b)?"

_RULES: list[Rule] = [
    # -- reused from the shared baseline ------------------------------------------------
    Rule("EMAIL", _BASELINE_PATTERNS["EMAIL"], 0.99),
    Rule("PHONE", _BASELINE_PATTERNS["PHONE"], 0.6),
    Rule("CARD_LIKE_NUMBER", _BASELINE_PATTERNS["CARD_LIKE_NUMBER"], 0.5, boosted=(luhn_valid, 0.95)),

    # -- email / phone variants -----------------------------------------------------------
    Rule("EMAIL", _r(r"(?i)\b[\w.]+(?:\s+dot\s+\w+)*\s+(?:at|@)\s+\w+(?:\s+dot\s+\w+)+\b"), 0.8),  # "john dot doe at gmail dot com"
    Rule("PHONE", _r(r"(?<!\d)(?:\d ){9,12}\d(?!\d)"), 0.6),                          # "0 7 7 1 2 3 4 5 6 7"
    Rule("PHONE", _r(r"(?<!\d)0\d{2}-\d{4}-\d{4}(?!\d)"), 0.7),                       # JP 090-2468-1357
    Rule("PHONE", _r(r"\(\d{2,4}\)\s?\d[\d ]{5,9}\d"), 0.65),                         # (011) 2 345 678

    # -- credentials ------------------------------------------------------------------------
    Rule("CREDENTIAL", _r(r"(?i)\b(?:password|passwd|pwd|passcode|pass|secret|api[_-]?key|access[_-]?token|"
                          r"token|auth|user(?:name)?|login|db_user|db_pass(?:word)?)\b(?:\s+is)?\s*[:=]\s*\S+"), 0.9),
    Rule("CREDENTIAL", _r(r"(?i)\b(?:password|passcode|pin)\s+is\s+\S+"), 0.8),
    Rule("CREDENTIAL", _r(r"(?i)\b(?:otp|cvv|cvc|pin|verification code|one[- ]time (?:code|password))\b[^\d\n]{0,15}\d{3,8}\b"), 0.85),
    Rule("CREDENTIAL", _r(r"\b(?:AKIA|ASIA|AIDA|AROA|AGPA)[0-9A-Z]{16}\b"), 0.95),   # AWS key / id prefixes
    Rule("CREDENTIAL", _r(r"\beyJ[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+){0,2}\b"), 0.9),   # JWT
    Rule("CREDENTIAL", _r(r"\b(?:sk|pk|rk|gh[a-z]|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b"), 0.9),
    Rule("CREDENTIAL", _r(r"\b[0-9a-f]{16,64}\b"), 0.45),                              # hex secret-ish (also hashes)

    # -- dates ------------------------------------------------------------------------------
    Rule("DATE", _r(r"(?<![\d./-])\d{1,2}[./-]\d{1,2}[./-]\d{2,4}(?!\d|[./-]\d)"), 0.8),
    Rule("DATE", _r(r"(?<![\d./-])\d{4}[./-]\d{1,2}[./-]\d{1,2}(?!\d|[./-]\d)"), 0.8),
    Rule("DATE", _r(r"\d{4}年\d{1,2}月\d{1,2}日"), 0.9),
    Rule("DATE", _r(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s{_MONTHS}(?:,?\s\d{{4}})?"), 0.8),
    Rule("DATE", _r(rf"\b{_MONTHS}\s\d{{1,2}}(?:st|nd|rd|th)?,?\s\d{{4}}\b"), 0.8),
    Rule("DATE", _r(r"\b\d{1,2}:\d{2}(?::\d{2})?\s?(?:[AP]M|IST|UTC|GMT|SLST)\b"), 0.5),   # times with zone

    # -- money ------------------------------------------------------------------------------
    Rule("FINANCIAL_FIGURE", _r(rf"\b{_CUR}\s?(?:{_AMOUNT})"), 0.85),
    Rule("FINANCIAL_FIGURE", _r(rf"[$€£₹¥]\s?(?:{_AMOUNT})"), 0.85),
    Rule("FINANCIAL_FIGURE", _r(rf"(?<![\d.,])(?:{_AMOUNT})\s?(?:USD|EUR|GBP|LKR|JPY|INR|AUD|CAD|SGD|AED)\b"), 0.8),

    # -- national IDs / passports (all countries share the NATIONAL_ID category) ---------
    Rule("NATIONAL_ID", _r(r"(?<![\dA-Za-z])\d{9}[VvXx](?![A-Za-z0-9])"), 0.9),        # LK NIC (old)
    Rule("NATIONAL_ID", _r(r"(?<![\d])\d{12}(?![\d])"), 0.45),                          # LK NIC (new) / ambiguous
    Rule("NATIONAL_ID", _r(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), 0.85),                  # US SSN
    Rule("NATIONAL_ID", _r(r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"), 0.75),   # UK NI (incl. the QQ 12 34 56 C doc example)
    Rule("NATIONAL_ID", _r(r"\b[STFGM]\d{7}[A-Z]\b"), 0.85),                            # SG NRIC
    Rule("NATIONAL_ID", _r(r"\b[A-Z]{5}\d{4}[A-Z]\b"), 0.85),                           # IN PAN
    Rule("NATIONAL_ID", _r(r"(?<!\d)\d{4}\s\d{4}\s\d{4}(?!\d)"), 0.6),                  # IN Aadhaar
    Rule("NATIONAL_ID", _r(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)"), 0.9),            # BR CPF
    Rule("NATIONAL_ID", _r(r"(?<!\d)\d{5}-\d{7}-\d(?!\d)"), 0.9),                       # PK CNIC
    Rule("NATIONAL_ID", _r(r"(?<!\d)\d{3}[- ]\d{3}[- ]\d{3}(?!\d)"), 0.5),              # CA SIN / ambiguous
    Rule("NATIONAL_ID", _r(r"\b[A-Z]\d{7,8}\b"), 0.55),                                 # passports (LK/IN/US-style)

    # -- bank identifiers -----------------------------------------------------------------
    Rule("BANK_ACCOUNT", _r(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?\b"), 0.5,
         validator=iban_valid, boosted=(iban_valid, 0.97)),
    Rule("BANK_ACCOUNT", _r(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b"), 0.7, validator=swift_valid),
    Rule("BANK_ACCOUNT", _r(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), 0.85),                         # IFSC
    Rule("BANK_ACCOUNT", _r(r"(?<!\d)\d{2}-\d{2}-\d{2}(?!\d)"), 0.45),                   # UK sort code
    Rule("BANK_ACCOUNT", _r(r"(?<!\d)\d{3}-\d{3}(?!\d)"), 0.35),                         # AU BSB
    Rule("BANK_ACCOUNT", _r(r"(?i)\b(?:acc(?:ount)?\.?|a/c)\s*(?:no\.?|number|num|#)?\s*[:#]?\s*(\d[\d -]{5,20}\d)"), 0.85, group=1),

    # -- network / URL tokens -----------------------------------------------------------
    Rule("IP_ADDRESS", _r(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.]\d)"), 0.5, validator=ipv4_valid, boosted=(ipv4_valid, 0.9)),
    Rule("IP_ADDRESS", _r(r"\b(?:[0-9a-fA-F]{1,4}:){4,7}[0-9a-fA-F]{1,4}\b"), 0.85),
    Rule("URL_TOKEN", _r(r"(?i)https?://[^\s\"']*[?&](?:token|session|sessionid|sid|auth|key|code|jwt|access_token)=[^\s\"']+"), 0.9),
    Rule("URL_TOKEN", _r(r"\bsess(?:ion)?_[A-Za-z0-9]{6,}\b"), 0.85),

    # -- medical ----------------------------------------------------------------------------
    Rule("MEDICAL", _r(rf"(?i)\b(?:{_MEDICAL_TERMS})\b{_DOSE}"), 0.7),
    Rule("MEDICAL", _r(r"\b(?:PT|MRN|MED|PID|RX)-\d{4,}\b"), 0.85),

    # -- vehicles -------------------------------------------------------------------------
    Rule("VEHICLE_PLATE", _r(r"\b[A-Z]{2,3}-\d{4}\b"), 0.55),
]


class RegexDetector:
    def detect(self, text: str) -> list[Candidate]:
        found: list[Candidate] = []
        for rule in _RULES:
            for m in rule.pattern.finditer(text):
                start, end = m.span(rule.group)
                if start < 0:
                    continue
                if rule.category == "EMAIL" and m.re is _BASELINE_PATTERNS["EMAIL"]:
                    # The shared pattern's tail class includes ".", so it swallows a
                    # sentence-ending period ("write to a@b.com."); trim it back off.
                    end = start + len(m.group(rule.group).rstrip(".-"))
                span = text[start:end]
                if rule.validator and not rule.validator(span):
                    continue
                conf = rule.confidence
                if rule.boosted and rule.boosted[0](span):
                    conf = rule.boosted[1]
                found.append(Candidate(start, end, rule.category, conf, "regex"))
        return _resolve_overlaps(found)


def _resolve_overlaps(cands: list[Candidate]) -> list[Candidate]:
    """Longest span wins, then higher confidence: a full IBAN or Luhn card beats the loose
    phone/ID patterns matching inside it, and a credential line beats an email embedded in it."""
    kept: list[Candidate] = []
    for c in sorted(cands, key=lambda c: (-(c.end - c.start), -c.confidence, c.start)):
        if not any(c.overlaps(k) for k in kept):
            kept.append(c)
    return sorted(kept, key=lambda c: c.start)

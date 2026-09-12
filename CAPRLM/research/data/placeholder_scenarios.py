"""PLACEHOLDER scenario generator (research decision #3 — dataset construction — is open).

Template sentences with entity slots, filled from small value pools and placed in a context
of some "kind". Gold annotations come from placeholder_labels.py. Train and test are
generated from disjoint value pools, and some context variants only appear in test, so the
split isn't just memorisation of strings — but see placeholder_labels.py for why that still
doesn't make the numbers reportable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from shared.types import PrivacyContext, Source

from .placeholder_labels import KINDS, PLACEHOLDER_DATASET_NAME, gold_tier


@dataclass(frozen=True)
class GoldSpan:
    surface: str
    category: str
    tier: str


@dataclass(frozen=True)
class Scenario:
    text: str
    ctx: PrivacyContext
    kind: str
    gold: tuple[GoldSpan, ...]


# kind -> context variants (app, domain, title). The last variant of each kind is test-only.
_CONTEXTS: dict[str, list[tuple[str, str | None, str]]] = {
    "banking": [("chrome", "secure.examplebank.com", "Account Summary"),
                ("chrome", "pay.examplewallet.com", "Transactions"),
                ("edge", "netbanking.examplebank.lk", "Fund Transfer")],
    "health": [("chrome", "portal.cityclinic.org", "Patient Records"),
               ("edge", "myhealth.examplehospital.com", "Lab Results"),
               ("chrome", "rx.examplepharmacy.com", "Prescription Refill")],
    "hr": [("excel", None, "Payroll_2026.xlsx"),
           ("chrome", "hr.examplecorp.com", "Employee Directory"),
           ("chrome", "jobs.examplecorp.com", "Recruitment Pipeline")],
    "email": [("outlook", None, "Inbox - Outlook"),
              ("chrome", "mail.google.com", "Inbox"),
              ("chrome", "mail.example.com", "Inbox")],
    "work": [("teams", None, "Project Alpha | Chat"),
             ("slack", None, "#dev-team"),
             ("chrome", "app.slack.com", "general")],
    "social": [("chrome", "www.facebook.com", "Home"),
               ("chrome", "www.reddit.com", "r/askreddit"),
               ("chrome", "www.linkedin.com", "Feed")],
    "code": [("chrome", "github.com", "Pull Request #42"),
             ("code", None, "main.py - Visual Studio Code"),
             ("chrome", "stackoverflow.com", "Question 12345")],
    "news": [("chrome", "www.bbc.com", "News"),
             ("chrome", "en.wikipedia.org", "Article"),
             ("chrome", "www.cnn.com", "World News")],
    "shopping": [("chrome", "www.amazon.com", "Checkout"),
                 ("chrome", "www.ebay.com", "Cart"),
                 ("chrome", "www.exampleshop.com", "Your Cart")],
    "notes": [("notepad", None, "notes.txt - Notepad"),
              ("onenote", None, "Personal Notes"),
              ("notepad", None, "todo-notes.txt - Notepad")],
}

_PERSONS = [("Nimal", "Perera"), ("Kavindi", "Fernando"), ("Sarah", "Johnson"), ("Michael", "Brown"),
            ("Priya", "Nair"), ("Daniel", "Wright"), ("Amali", "Silva"), ("Thomas", "Hughes"),
            ("Anna", "Kowalski"), ("Ravi", "Kumar"), ("Emily", "Clark"), ("Hasini", "Jayawardena"),
            ("George", "Miller"), ("Lucia", "Moretti"), ("Omar", "Haddad"), ("Chloe", "Martin")]
_EMAIL_DOMAINS = ["example.com", "mailhost.net", "corp.example.org", "inbox.example.lk"]
_LOCATIONS = ["Colombo", "Kandy", "Berlin", "Seattle", "Toronto", "Galle", "Manchester", "Sydney",
              "Lisbon", "Osaka"]
_ORGS = ["Acme Corporation", "Globex Ltd", "Initech", "Umbrella Holdings", "Contoso Inc",
         "Hooli", "Stark Industries", "Wayne Enterprises"]
_DATES = ["March 3, 1990", "12 May 2025", "July 21, 1984", "2 January 2026", "October 9, 1975",
          "5 August 2024", "June 30, 2001", "November 14, 1998"]
_MONEY = ["$4,500", "LKR 250,000", "$12,300", "EUR 8,750", "$980", "USD 45,000", "$2,150", "GBP 6,400"]
_SECRETS = ["Xk29!fQ7m", "Tr0ub4dor&3z", "pa55w0rd-Zq81", "Gh7#kLm92v"]

_TEMPLATES: dict[str, list[str]] = {
    "EMAIL": ["Please send the report to {v} as soon as possible.",
              "You can reach the team at {v} if anything is unclear.",
              "Forward the invoice to {v} and copy the finance team."],
    "PHONE": ["Call me on {v} when you are free.",
              "The contact number on file is {v}.",
              "Reach the front desk at {v} before noon."],
    "PERSON": ["Hi {v}, thanks for getting back to me.",
               "The request was submitted by {v} for the project.",
               "Please ask {v} to review the attached file."],
    "LOCATION": ["The meeting will be held in {v}.",
                 "Packages are shipped from the depot in {v}.",
                 "She recently moved to {v} for work."],
    "ORG": ["The contract was signed with {v}.",
            "He works at {v} on the platform team.",
            "Our partner {v} confirmed the schedule."],
    "DATE": ["The date of birth on record is {v}.",
             "The appointment is booked for {v}.",
             "This entry was last updated on {v}."],
    "FINANCIAL_FIGURE": ["The outstanding balance is {v}.",
                         "Approved budget for the project: {v}.",
                         "Your total comes to {v}."],
    "CARD_LIKE_NUMBER": ["Card on file: {v}.",
                         "Please charge {v} for the order.",
                         "Payment method {v} was declined."],
    "CREDENTIAL": ["Login details are password: {v}",
                   "Use password={v} to authenticate the service."],
}
_FILLERS = ["Let me know if that works.", "Thanks again for the help.", "I will follow up soon.",
            "No further action is needed.", "See the notes below for details."]

# Relative frequency of each category in a scenario.
_CATEGORY_WEIGHTS = {"EMAIL": 3, "PHONE": 2, "PERSON": 4, "LOCATION": 3, "ORG": 2, "DATE": 2,
                     "FINANCIAL_FIGURE": 3, "CARD_LIKE_NUMBER": 1, "CREDENTIAL": 1}


def _luhn_card(rng: random.Random) -> str:
    digits = [4] + [rng.randint(0, 9) for _ in range(14)]
    total = 0
    for i, d in enumerate(reversed(digits)):   # check digit not yet appended: doubling starts at i=0
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    digits.append((10 - total % 10) % 10)
    s = "".join(map(str, digits))
    sep = rng.choice([" ", "-"])
    return sep.join(s[i:i + 4] for i in range(0, 16, 4))


def _phone(rng: random.Random) -> str:
    return rng.choice([
        f"077-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
        f"+94 77 {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
        f"011 {rng.randint(200, 999)} {rng.randint(1000, 9999)}",
        f"(555) {rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
    ])


def _split(pool: list, is_test: bool) -> list:
    """Disjoint value pools: even indices train, odd test."""
    return pool[1::2] if is_test else pool[0::2]


def _value(category: str, rng: random.Random, is_test: bool) -> str:
    if category == "EMAIL":
        first, last = rng.choice(_split(_PERSONS, is_test))
        return f"{first.lower()}.{last.lower()}@{rng.choice(_EMAIL_DOMAINS)}"
    if category == "PHONE":
        return _phone(rng)
    if category == "PERSON":
        first, last = rng.choice(_split(_PERSONS, is_test))
        return f"{first} {last}"
    if category == "LOCATION":
        return rng.choice(_split(_LOCATIONS, is_test))
    if category == "ORG":
        return rng.choice(_split(_ORGS, is_test))
    if category == "DATE":
        return rng.choice(_split(_DATES, is_test))
    if category == "FINANCIAL_FIGURE":
        return rng.choice(_split(_MONEY, is_test))
    if category == "CARD_LIKE_NUMBER":
        return _luhn_card(rng)
    if category == "CREDENTIAL":
        return rng.choice(_split(_SECRETS, is_test))
    raise ValueError(category)


def generate(n: int, seed: int, test: bool) -> list[Scenario]:
    rng = random.Random(seed)
    cats = list(_CATEGORY_WEIGHTS)
    weights = [_CATEGORY_WEIGHTS[c] for c in cats]
    scenarios: list[Scenario] = []
    for _ in range(n):
        kind = rng.choice(KINDS)
        variants = _CONTEXTS[kind]
        app, domain, title = rng.choice(variants if test else variants[:-1])
        ctx = PrivacyContext(app=app, domain=domain, url=None, title=title, source=Source.UIA)

        sentences: list[str] = []
        gold: list[GoldSpan] = []
        seen: set[str] = set()
        for category in rng.choices(cats, weights, k=rng.randint(1, 3)):
            value = _value(category, rng, test)
            if value in seen:
                continue
            seen.add(value)
            sentences.append(rng.choice(_TEMPLATES[category]).format(v=value))
            gold.append(GoldSpan(value, category, gold_tier(category, kind)))
        if rng.random() < 0.5:
            sentences.append(rng.choice(_FILLERS))
        scenarios.append(Scenario(" ".join(sentences), ctx, kind, tuple(gold)))
    return scenarios


__all__ = ["GoldSpan", "Scenario", "generate", "PLACEHOLDER_DATASET_NAME"]

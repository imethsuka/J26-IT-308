# Privacy filter handoff — PCCILM ↔ CAPRLM

This is the interface between Member 1's acquisition pipeline and Member 2's privacy
processing. It is shared infrastructure, not either member's research content — see
`shared/` for the code, `research/` for where Wijesingha's actual model work goes.

## The interface

```python
class PrivacyFilter(Protocol):
    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult: ...

class UrlFilter(Protocol):
    def filter_url(self, url: str) -> str: ...

PrivacyContext: app, domain, url, title, source ("uia" | "ocr")
FilterResult:   text, blocked, redactions, filter_version, audit[]
RedactionRecord: category, risk_tier, action   # no raw content — category + action only
```

Full definitions: `shared/types.py`, `shared/privacy_filter.py`.

## Example

Input:
```
text = "Contact john.doe@example.com or call 077-123-4567 about the invoice."
ctx  = PrivacyContext(app="chrome", domain="mail.example.com", title="Inbox", source=Source.UIA)
```

Rule-based baseline output (`shared/rule_based_filter.py`):
```
text = "Contact [EMAIL_1] or call [PHONE_1] about the invoice."
blocked = False
redactions = 2
filter_version = "rule-based/0.1"
audit = [RedactionRecord(category="EMAIL", risk_tier="medium", action="tokenize"),
         RedactionRecord(category="PHONE", risk_tier="medium", action="tokenize")]
```

## Fail-closed rules (non-negotiable)

- **No filter, no persisted text.** If no filter is installed, or a filter call raises,
  the caller treats the content as blocked. The raw text stays in the encrypted,
  TTL-bounded raw buffer; it never reaches `memory_entries` unprotected.
- **Dev bypass is explicit and scoped.** `shared/passthrough_filter.py` only runs when
  constructed with `acknowledge_dev_mode_on_own_data=True`, and only on your own
  captured data — there's no consent path yet for anyone else's.
- **The audit trail never carries raw sensitive content** — category and action only,
  so evaluation (precision/recall, leakage) can be computed without a second copy of
  sensitive text sitting in logs.

## Where the real model goes

Everything under `research/` is Wijesingha's — detection, risk-prediction model,
dataset, training, evaluation. This handoff doc and `shared/` are the only things either
side should need to import from the other.

## Open questions for the real implementation (not yet answered)

1. Reversible tokenization vs. irreversible redaction per category, and who holds any
   token/key store (local-only, never crossing to Member 1's code).
2. Latency budget — restated per merged document (the actual call pattern), not the
   original per-capture figure.
3. Whether `audit` (category + action counts) is sufficient for the evaluation table,
   or a more detailed redaction log is needed.
4. `filter_image` (faces / documents in screenshots) — in scope per Wijesingha's own
   proposal's multimodal detection claim; needs its own hook, not deferred indefinitely.
   *Hook added:* `ImageFilter` in `shared/privacy_filter.py` (separate Protocol, so
   `PrivacyFilter` is unchanged); `research/caprlm_filter.py` implements it as OCR → text
   filter. Faces / document detection (the CV half) is still not done. See `BUILD_LOG.md`.

import re


# Detection rules
_PATTERNS = {
	"EMAIL": re.compile(
		r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
		r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
		r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\b"
	),
	"PHONE": re.compile(
		r"(?<!\w)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)"
	),
	"URL": re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE),
	"IP_ADDRESS": re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
	"DATE": re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b"),
	"WINDOWS_PATH": re.compile(
		r"(?<![A-Za-z0-9])(?:[A-Za-z]:\\|\\\\[^\\/\s]+\\)"
		r"(?:[^<>:\"/\\|?*\r\n]+\\)*[^<>:\"/\\|?*\r\n]+"
	),
	"PASSWORD": re.compile(
		r"(?i)\b(?:password|passwd|pwd|passcode)\s*[:=]\s*"
		r"(?![<\[]?(?:redacted|hidden|masked)[>\]]?)([^\s,;]+)"
	),
	"API_KEY": re.compile(
		r"(?i)\b(?:api[_ -]?key|access[_ -]?key|client[_ -]?secret)\s*[:=]\s*"
		r"([A-Za-z0-9][A-Za-z0-9._~+/=-]{15,})"
	),
	"BEARER_TOKEN": re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{20,})"),
	"JWT": re.compile(
		r"\b[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\b"
	),
	"AWS_ACCESS_KEY": re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
	"GOOGLE_API_KEY": re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),
	"GITHUB_TOKEN": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
}

_EMAIL_OBFUSCATED_PATTERNS = (
	re.compile(
		r"(?i)(?<![\w.+-])[A-Z0-9][A-Z0-9._%+-]{0,63}\s*"
		r"(?:\[at\]|\(at\)|\{at\})\s*[A-Z0-9-]+"
		r"(?:\s*(?:\[dot\]|\(dot\)|\{dot\})\s*[A-Z0-9-]+)+"
		r"(?![\w.-])"
	),
	re.compile(
		r"(?i)(?<![\w.+-])[A-Z0-9][A-Z0-9._%+-]{0,63}\s*@\s*[A-Z0-9-]+"
		r"(?:\s+\.\s+|\s*\.\s*[A-Z0-9-]+(?:\s+\.\s+))"
		r"[A-Z0-9-]+(?![\w.-])"
	),
)

_DETECTION_CATEGORIES = tuple(_PATTERNS) + ("EMAIL_OBFUSCATED",)


# Match normalization
def _valid_ipv4(value: str) -> bool:
	return all(0 <= int(octet) <= 255 for octet in value.split("."))


def _matches_for_category(category: str, text: str) -> list[str]:
	if category == "EMAIL_OBFUSCATED":
		return list(dict.fromkeys(
			match.group(0)
			for pattern in _EMAIL_OBFUSCATED_PATTERNS
			for match in pattern.finditer(text)
		))

	matches = [
		match.group(1) if category in {"PASSWORD", "API_KEY", "BEARER_TOKEN"}
		else match.group(0)
		for match in _PATTERNS[category].finditer(text)
	]
	if category == "IP_ADDRESS":
		matches = [match for match in matches if _valid_ipv4(match)]
	if category == "URL":
		matches = [match.rstrip(".,;:!?)]}") for match in matches]
	return matches


# Public detection API
def detect_sensitive_entities(ocr_text: str) -> dict[str, object]:
	"""Return deterministic sensitive-entity matches and counts from OCR text."""
	if not isinstance(ocr_text, str):
		raise TypeError("ocr_text must be a string")

	result: dict[str, object] = {}
	total_count = 0
	for category in _DETECTION_CATEGORIES:
		matches = _matches_for_category(category, ocr_text)
		result[category] = {"matches": matches, "count": len(matches)}
		total_count += len(matches)

	result["total_sensitive_entity_count"] = total_count
	return result

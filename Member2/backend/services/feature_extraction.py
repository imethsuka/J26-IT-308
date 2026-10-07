from collections.abc import Mapping


# Detection output access
def _get_category_count(
	detection_output: Mapping[str, object], category: str
) -> int:
	category_output = detection_output.get(category)
	if not isinstance(category_output, Mapping):
		raise ValueError(f"Missing detection output for category: {category}")

	count = category_output.get("count")
	if not isinstance(count, int) or isinstance(count, bool):
		raise ValueError(f"Invalid detection count for category: {category}")
	return count


def _get_total_count(detection_output: Mapping[str, object]) -> int:
	total_count = detection_output.get("total_sensitive_entity_count")
	if not isinstance(total_count, int) or isinstance(total_count, bool):
		raise ValueError("Invalid total sensitive entity count")
	return total_count


# Public feature extraction API
def extract_features(
	ocr_text: str,
	detection_output: Mapping[str, object],
	application_context: object,
) -> dict[str, object]:
	"""Build prototype features from OCR text and existing detection output."""
	if not isinstance(ocr_text, str):
		raise TypeError("ocr_text must be a string")
	if not isinstance(detection_output, Mapping):
		raise TypeError("detection_output must be a mapping")

	return {
		"email_count": _get_category_count(detection_output, "EMAIL"),
		"phone_count": _get_category_count(detection_output, "PHONE"),
		"url_count": _get_category_count(detection_output, "URL"),
		"ip_count": _get_category_count(detection_output, "IP_ADDRESS"),
		"date_count": _get_category_count(detection_output, "DATE"),
		"windows_path_count": _get_category_count(
			detection_output, "WINDOWS_PATH"
		),
		"sensitive_entity_count": _get_total_count(detection_output),
		"ocr_text_length": len(ocr_text),
		"application_context": application_context,
	}

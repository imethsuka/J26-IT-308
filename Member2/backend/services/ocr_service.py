from os import PathLike
from pathlib import Path


# Image input validation
def extract_text(image_path: str | PathLike[str]) -> tuple[str, int]:
	"""Extract text from an image and return the text with its detection count."""
	path = Path(image_path)
	if not path.is_file():
		raise ValueError(f"Image file does not exist: {path}")

	try:
		with path.open("rb") as image_file:
			if not image_file.read(1):
				raise ValueError(f"Image file is empty or unreadable: {path}")
	except OSError as error:
		raise ValueError(f"Image file is unreadable: {path}") from error

	# OCR processing
	try:
		import easyocr
	except ImportError as error:
		raise RuntimeError(
			"EasyOCR is not installed or importable in the current environment."
		) from error

	reader = easyocr.Reader(["en"])
	detections = reader.readtext(str(path))
	extracted_text = "\n".join(detection[1] for detection in detections)
	return extracted_text, len(detections)

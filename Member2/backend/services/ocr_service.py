from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import easyocr as _easyocr

# Module-level reader singleton — initialised once on first call.
_reader: "_easyocr.Reader | None" = None

# OCR parameters kept in sync with the batch pipeline (process_screenshots.py).
_CANVAS_SIZE = 1536
_MAG_RATIO = 0.8


def _get_reader() -> "_easyocr.Reader":
    global _reader
    if _reader is None:
        try:
            import easyocr
        except ImportError as error:
            raise RuntimeError(
                "EasyOCR is not installed or importable in the current environment."
            ) from error
        _reader = easyocr.Reader(["en"])
    return _reader


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

    reader = _get_reader()
    detections = reader.readtext(
        str(path), canvas_size=_CANVAS_SIZE, mag_ratio=_MAG_RATIO
    )
    extracted_text = "\n".join(detection[1] for detection in detections)
    return extracted_text, len(detections)

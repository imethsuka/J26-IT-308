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
def _validate_image_path(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"Image file does not exist: {path}")
    try:
        with path.open("rb") as fh:
            if not fh.read(1):
                raise ValueError(f"Image file is empty or unreadable: {path}")
    except OSError as error:
        raise ValueError(f"Image file is unreadable: {path}") from error


def extract_text(image_path: str | PathLike[str]) -> tuple[str, int]:
    """Extract text from an image and return the text with its detection count."""
    path = Path(image_path)
    _validate_image_path(path)
    reader = _get_reader()
    detections = reader.readtext(
        str(path), canvas_size=_CANVAS_SIZE, mag_ratio=_MAG_RATIO
    )
    extracted_text = "\n".join(detection[1] for detection in detections)
    return extracted_text, len(detections)


def extract_text_with_boxes(
    image_path: str | PathLike[str],
) -> tuple[str, int, list[tuple[list, str, float]]]:
    """
    Extract text and bounding boxes from an image.

    EasyOCR returns each detection as (bbox, text, confidence) where
    bbox is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] (top-left → clockwise).

    Returns
    -------
    (extracted_text, detection_count, detections)
        extracted_text   : full OCR text joined by newlines
        detection_count  : number of text regions found
        detections       : list of (bbox, text, confidence) tuples
    """
    path = Path(image_path)
    _validate_image_path(path)
    reader = _get_reader()
    raw = reader.readtext(str(path), canvas_size=_CANVAS_SIZE, mag_ratio=_MAG_RATIO)
    detections: list[tuple[list, str, float]] = [
        (d[0], d[1], float(d[2])) for d in raw
    ]
    extracted_text = "\n".join(d[1] for d in detections)
    return extracted_text, len(detections), detections

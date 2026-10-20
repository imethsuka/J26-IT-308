"""OCR for Source.OCR content (screenshots): image bytes -> text, run before NER.

Tesseract via pytesseract is the default engine. It needs the Tesseract binary installed
separately (Windows: https://github.com/UB-Mannheim/tesseract/wiki, or set TESSERACT_CMD).
When it is missing this raises OcrUnavailableError so the caller fails closed.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Protocol

_WINDOWS_DEFAULT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


class OcrUnavailableError(RuntimeError):
    pass


class OcrEngine(Protocol):
    def image_to_text(self, image: bytes) -> str: ...


class TesseractOcr:
    def image_to_text(self, image: bytes) -> str:
        try:
            import pytesseract
            from PIL import Image

            cmd = os.environ.get("TESSERACT_CMD") or (str(_WINDOWS_DEFAULT) if _WINDOWS_DEFAULT.exists() else None)
            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
            with Image.open(io.BytesIO(image)) as img:
                return pytesseract.image_to_string(img)
        except Exception as exc:  # missing binary, missing package, undecodable image
            raise OcrUnavailableError(f"OCR failed ({type(exc).__name__}): {exc}") from exc

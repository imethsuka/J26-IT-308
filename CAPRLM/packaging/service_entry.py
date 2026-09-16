r"""Entry point of the frozen CAPRLM service (``caprlm-service.exe``), started by the dashboard.

    caprlm-service.exe [--port 8787] [--host 127.0.0.1]

Installed layout (see packaging/build_service.ps1)::

    caprlm\caprlm-service.exe  (+ _internal\)    this program, spaCy model and risk model inside
    caprlm\tesseract\tesseract.exe               the OCR binary, with tessdata\

Nothing is written next to the program: the saved risk model, the policy / corrections / activity file all
live in ``%LOCALAPPDATA%\\context-importance\\caprlm``. From the repo, ``python -m uvicorn research.api.service:app``
still works and uses the repo's own paths.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "context-importance" / "caprlm"


def _prepare_environment() -> None:
    frozen = getattr(sys, "frozen", False)
    here = Path(sys.executable).parent if frozen else Path(__file__).resolve().parent
    bundle = Path(getattr(sys, "_MEIPASS", here))

    data = _data_dir()
    data.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CAPRLM_STATE_PATH", str(data / "profile.json"))

    # The risk model: keep a copy in the data folder (the service may retrain and save it), seeded from the shipped one.
    model = data / "caprlm_model.joblib"
    shipped = bundle / "artifacts" / "caprlm_model.joblib"
    if not model.exists() and shipped.exists():
        shutil.copyfile(shipped, model)
    os.environ.setdefault("CAPRLM_MODEL_PATH", str(model))

    # spaCy cannot find a package by name inside a frozen app, but loads the model folder by path.
    model_dir = bundle / "en_core_web_sm" / "en_core_web_sm-3.8.0"
    if model_dir.exists():
        os.environ.setdefault("CAPRLM_SPACY_MODEL", str(model_dir))

    # Bundled Tesseract (screenshots -> text). Missing is fine: the image endpoint then answers 503 and blocks.
    tesseract = here / "tesseract" / "tesseract.exe"
    if tesseract.exists():
        os.environ.setdefault("TESSERACT_CMD", str(tesseract))
        os.environ.setdefault("TESSDATA_PREFIX", str(tesseract.parent / "tessdata"))


def main() -> int:
    parser = argparse.ArgumentParser(description="CAPRLM local privacy service")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1", help="loopback only by default; the service has no authentication")
    args = parser.parse_args()

    _prepare_environment()

    import uvicorn
    from research.api.service import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

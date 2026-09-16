# PyInstaller spec for the CAPRLM service: one folder, caprlm-service.exe + its libraries.
# Build with packaging/build_service.ps1 (it uses packaging/.venv-build, which has no torch).
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

CAPRLM = Path(SPECPATH).parent

hidden = (
    collect_submodules("research") + collect_submodules("shared")
    + ["en_core_web_sm", "sklearn.utils._typedefs", "sklearn.neighbors._partition_nodes",
       "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
       "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on", "pytesseract"]
)
datas = collect_data_files("en_core_web_sm")
model = CAPRLM / "research" / "artifacts" / "caprlm_model.joblib"
if model.exists():
    datas.append((str(model), "artifacts"))
results = CAPRLM / "research" / "eval" / "results.json"
if results.exists():
    datas.append((str(results), "research/eval"))

a = Analysis(
    [str(Path(SPECPATH) / "service_entry.py")],
    pathex=[str(CAPRLM)],
    hiddenimports=hidden,
    datas=datas,
    excludes=["torch", "torchvision", "tkinter", "matplotlib", "IPython", "notebook", "jupyter", "pytest",
              "scipy.tests", "sklearn.tests", "tensorboard"],
    noarchive=False,
)
exe = EXE(PYZ(a.pure), a.scripts, [], exclude_binaries=True, name="caprlm-service",
          console=True, upx=False)   # console subsystem so the launcher's log redirect captures output
COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="caprlm-service")

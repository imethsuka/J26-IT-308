# PyInstaller spec: two executables (worker.exe, adapt.exe) sharing one folder, so the libraries are stored once.
# Built from packaging/.venv-build, which has no torch, so none can be pulled in by accident.
from pathlib import Path

SRC = str(Path(SPECPATH).parent / "python-worker")
EXCLUDES = ["torch", "torchvision", "mobileclip", "timm", "open_clip", "tkinter", "matplotlib", "IPython",
            "notebook", "jupyter", "pytest", "onnx", "tensorboard", "scipy.tests", "sklearn.tests", "pandas.tests"]
HIDDEN = ["lightgbm", "sklearn.cluster", "sklearn.utils._typedefs", "river.drift", "onnxruntime",
          # challenger models (models/challengers.py), imported lazily
          "sklearn.linear_model", "sklearn.ensemble", "sklearn.neural_network", "sklearn.pipeline",
          "sklearn.preprocessing", "river.forest", "river.tree", "joblib"]

def analysis(entry):
    return Analysis([str(Path(SPECPATH) / entry)], pathex=[SRC], hiddenimports=HIDDEN, excludes=EXCLUDES, noarchive=False)

worker_a, adapt_a = analysis("worker_entry.py"), analysis("adapt_entry.py")
MERGE((worker_a, "worker", "worker"), (adapt_a, "adapt", "adapt"))

def exe(a, name, console):
    return EXE(PYZ(a.pure), a.scripts, [], exclude_binaries=True, name=name, console=console, upx=False)

worker_exe = exe(worker_a, "worker", console=True)   # console subsystem so logs reach the launcher's file redirect
adapt_exe = exe(adapt_a, "adapt", console=True)
COLLECT(worker_exe, worker_a.binaries, worker_a.datas, adapt_exe, adapt_a.binaries, adapt_a.datas,
        strip=False, upx=False, name="worker")

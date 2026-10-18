"""Where the memory-writing model runs: a dedicated GPU when one is usable right now, otherwise the CPU.

The model is llama.cpp's own server (``llama-server.exe``, the Vulkan build fetched by ``packaging/fetch_llama.ps1``),
started on 127.0.0.1 only while memories are being written and stopped afterwards, so nothing stays in RAM or VRAM between
runs. Vulkan works through the normal graphics driver on NVIDIA, AMD and Intel Arc cards, and the same build carries the CPU
backends, so one download covers every PC.

Choosing the device, every time a model is started:

* **On battery: CPU.** Gaming laptops often switch the dedicated GPU off when unplugged, and when they don't, running it
  drains the battery.
* **Integrated graphics are skipped** (Intel UHD/Iris, AMD "Radeon Graphics" APUs, virtual adapters): they share system RAM
  and are rarely faster than the CPU for a small model.
* A dedicated GPU is used when its **free** memory holds at least half the model. llama.cpp then decides how many layers
  go on it (``--fit``, keeping ``FIT_MARGIN_MIB`` free): all of a small model, most of a 4B model on a 4 GB laptop GPU, the
  rest on the CPU. Measured: Qwen3.5-4B (2.7 GB) did not fit whole in 3.3 GB free and ran at ~53 s per memory on the CPU.
* **Which model:** ``model_path`` may be one .gguf file or a folder of them. With a folder, the GPU gets the largest model
  it can take and the CPU gets the smallest: measured on the author's laptop, Qwen3.5-4B wrote the best memories at ~3.4 s
  each on the RTX 2050 but took ~53 s each on the CPU, where Qwen3.5-2B is the sensible choice.
* If the GPU fails to start or fails mid-run (driver reset, the card switched off), the server is restarted on the **CPU**
  and the request retried once.

While it runs it stays out of the way: the server runs at low priority with a few CPU threads. In the background job it
**pauses** (:class:`~adaptation.summarizer.ModelPaused`) as soon as the user is back or the PC goes on battery; what is left
waits for the next idle time.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .summarizer import ModelPaused

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
DEFAULT_CTX = 4096
MIN_GPU_SHARE = 0.5             # the GPU must hold at least this share of the model to be worth using
FIT_MARGIN_MIB = 512            # what --fit leaves free on the GPU (the desktop and other apps need some)
USER_BACK_SECONDS = 30          # background runs pause when there was input this recently
START_TIMEOUT_S = 180.0
REQUEST_TIMEOUT_S = 600.0       # one memory on a slow CPU can take a while

_SERVER_ENV = "CONTEXT_IMPORTANCE_LLAMA_SERVER"
_DEVICE_LINE = re.compile(r"^\s*(?P<name>\w+\d+):\s*(?P<desc>.+?)\s*\((?P<total>\d+)\s*MiB,\s*(?P<free>\d+)\s*MiB free\)")
_DEDICATED = re.compile(r"geforce|quadro|\brtx\b|\bgtx\b|tesla|radeon\s+(rx|pro)|\barc\(tm\)\s+[ab]\d|\barc\s+[ab]\d|instinct", re.I)
_NOT_A_GPU = re.compile(r"microsoft basic|basic render|parsec|virtual|llvmpipe|swiftshader|remote|indirect display", re.I)


@dataclass(frozen=True)
class Device:
    name: str           # what llama-server's --device takes, e.g. "Vulkan1"
    description: str    # e.g. "NVIDIA GeForce RTX 2050"
    total_mib: int
    free_mib: int

    @property
    def dedicated(self) -> bool:
        """A card of its own. Integrated graphics name themselves "... Graphics" and share system RAM."""
        if _NOT_A_GPU.search(self.description):
            return False
        return bool(_DEDICATED.search(self.description)) or "graphics" not in self.description.lower()


def parse_devices(text: str) -> list[Device]:
    """The devices in ``llama-server --list-devices`` output."""
    found = []
    for line in text.splitlines():
        m = _DEVICE_LINE.match(line)
        if m:
            found.append(Device(m["name"], m["desc"], int(m["total"]), int(m["free"])))
    return found


def choose_device(devices: list[Device], *, power: str, model_bytes: int) -> Device | None:
    """The dedicated GPU to use, or ``None`` for the CPU (see the module docstring for the rules)."""
    if power == "battery":
        return None
    need_mib = MIN_GPU_SHARE * model_bytes / MIB + FIT_MARGIN_MIB
    usable = [d for d in devices if d.dedicated and d.free_mib >= need_mib]
    return max(usable, key=lambda d: d.free_mib, default=None)


def find_llama_server() -> Path | None:
    """``llama-server.exe``: an explicit override, the installed bundle (``<bundle>\\llm``), or the repo's fetched copy."""
    candidates = []
    if os.environ.get(_SERVER_ENV):
        candidates.append(Path(os.environ[_SERVER_ENV]))
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent.parent / "llm" / "llama-server.exe")
    candidates.append(Path(__file__).resolve().parents[2] / "packaging" / "llm-runtime" / "llama-server.exe")
    return next((c for c in candidates if c.is_file()), None)


def default_threads() -> int:
    """A third of the logical cores (at least 2): enough to make progress, leaving the rest of the PC responsive."""
    return max(2, (os.cpu_count() or 4) // 3)


def _no_window_low_priority() -> int:
    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS


def list_devices(server: Path) -> list[Device]:
    try:
        out = subprocess.run([str(server), "--list-devices"], capture_output=True, text=True, timeout=60,
                             creationflags=_no_window_low_priority())
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not list GPUs (%s); using the CPU", type(exc).__name__)
        return []
    return parse_devices(out.stdout + "\n" + out.stderr)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamaServerModel:
    """One running ``llama-server`` on loopback, on one device. :meth:`close` stops it."""

    def __init__(self, model_path: Path, server: Path, *, device: Device | None, n_ctx: int = DEFAULT_CTX,
                 threads: int | None = None, start_timeout: float = START_TIMEOUT_S) -> None:
        self.name = model_path.stem
        self.device = device
        self.port = _free_port()
        args = [str(server), "-m", str(model_path), "--host", "127.0.0.1", "--port", str(self.port), "-c", str(n_ctx),
                "-np", "1", "-t", str(threads or default_threads()), "--prio", "-1"]
        # On a GPU, -ngl is left unset so --fit chooses how many layers fit (all of them when the model is small).
        args += ["--device", device.name, "--fit", "on", "--fit-target", str(FIT_MARGIN_MIB)] if device             else ["--device", "none", "-ngl", "0"]
        # Its log can include request details, so it is never written anywhere: only the last lines are kept, in memory,
        # to explain a failed start.
        self._tail: deque[str] = deque(maxlen=15)
        self._process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                         text=True, errors="replace", creationflags=_no_window_low_priority())
        threading.Thread(target=self._drain, daemon=True).start()
        try:
            self._wait_ready(start_timeout)
        except BaseException:
            self.close()
            raise

    @property
    def where(self) -> str:
        return self.device.description if self.device else "CPU"

    def _drain(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            self._tail.append(line.rstrip())

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"llama-server exited ({self._process.returncode}) on {self.where}: " + " | ".join(self._tail)[-400:])
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass    # still loading (503) or not listening yet
            time.sleep(0.5)
        raise TimeoutError(f"llama-server did not become ready on {self.where} within {timeout:.0f}s")

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        body = json.dumps({
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0.2,
            # Qwen3/3.5 reason out loud first unless told not to: slower, and the reasoning could end up in a memory.
            # Chat templates without a thinking switch ignore it.
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode("utf-8")
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"] or ""

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()


class LocalModel:
    """The model the summary and rollup stages share for one run: started on first use, on the best device for the PC's
    state at that moment, moved to the CPU when the GPU fails or the PC is unplugged, and stopped by :meth:`close`.

    ``background=True`` (the scheduled job) also pauses it when the user comes back or the PC goes on battery.
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        background: bool,
        server: Path | None = None,
        power_fn: Callable[[], str] | None = None,
        idle_fn: Callable[[], float] | None = None,
        devices_fn: Callable[[Path], list[Device]] = list_devices,
        start_fn: Callable[..., Any] = LlamaServerModel,
    ) -> None:
        from . import host_state

        self.path = Path(model_path)
        found = sorted(self.path.glob("*.gguf"), key=lambda p: p.stat().st_size) if self.path.is_dir() else [self.path]
        if not found:
            raise FileNotFoundError(f"no .gguf model in {self.path}")
        self.models = found                     # smallest first
        self.name = found[-1].stem              # until it starts: the model a GPU would get
        self.background = background
        self._server = server if server is not None else find_llama_server()
        self._power = power_fn or host_state.power_status
        self._idle = idle_fn or host_state.seconds_since_last_input
        self._devices = devices_fn
        self._start = start_fn
        self._model: Any = None
        self.where: str | None = None

    def __call__(self) -> "LocalModel":
        """The stages take a factory; the model itself starts lazily, on the first request."""
        return self

    def _launch(self, *, cpu_only: bool = False) -> None:
        self.close()
        smallest = self.models[0]
        if self._server is None:
            # No llama.cpp server here (a dev setup): the in-process binding, CPU only.
            from .summarizer import LlamaCppModel

            self._model, self.where, self.name = LlamaCppModel(smallest, n_threads=default_threads()), "CPU", smallest.stem
            logger.info("memory model %s on the CPU (llama-cpp-python)", self.name)
            return
        device, model = None, smallest
        if not cpu_only:
            devices, power = self._devices(self._server), self._power()
            for candidate in reversed(self.models):             # the largest model a GPU can take
                device = choose_device(devices, power=power, model_bytes=candidate.stat().st_size)
                if device is not None:
                    model = candidate
                    break
        try:
            self._model = self._start(model, self._server, device=device)
        except (RuntimeError, TimeoutError, OSError) as exc:
            if device is None:
                raise
            logger.warning("memory model could not start on %s (%s); using the CPU", device.description, exc)
            model = smallest
            self._model = self._start(model, self._server, device=None)
        self.where, self.name = self._model.where, model.stem
        logger.info("memory model %s on %s", self.name, self.where)

    @property
    def size_bytes(self) -> int:
        """The largest model this may load: what the light-load check must leave room for."""
        return self.models[-1].stat().st_size

    def _check(self) -> None:
        on_battery = self._power() == "battery"
        if self.background:
            if self._idle() < USER_BACK_SECONDS:
                raise ModelPaused("the user is back")
            if on_battery:
                raise ModelPaused("the PC is on battery")
        if self._model is not None and on_battery and getattr(self._model, "device", None) is not None:
            logger.info("unplugged: moving the memory model from %s to the CPU", self.where)
            self._launch(cpu_only=True)

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        self._check()
        if self._model is None:
            self._launch()
        try:
            return self._model.complete(system, user, max_tokens=max_tokens)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            if getattr(self._model, "device", None) is None:
                raise
            logger.warning("memory model failed on %s (%s); retrying on the CPU", self.where, type(exc).__name__)
            self._launch(cpu_only=True)
            return self._model.complete(system, user, max_tokens=max_tokens)

    def close(self) -> None:
        if self._model is not None and hasattr(self._model, "close"):
            self._model.close()
        self._model = None

"""Host readiness checks: is the device idle, and is it on AC power?

The scheduled adaptation job is gated on both so it never trains while the user is actively working
or running on battery. These are real Windows implementations (GetLastInputInfo / GetSystemPower
Status via ctypes, no third-party dependency) and are injectable so the job can be tested with
controlled state.

On non-Windows hosts the native calls are unavailable; ``seconds_since_last_input`` and
``power_status`` raise, and the job should be given injected providers instead.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

# ACLineStatus values from GetSystemPowerStatus.
_AC_OFFLINE = 0
_AC_ONLINE = 1
_AC_UNKNOWN = 255


@dataclass(frozen=True)
class HostReadiness:
    ready: bool
    idle: bool
    on_ac_power: bool
    seconds_idle: float
    power: str            # "ac", "battery", or "unknown"
    reason: str


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    class _SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def seconds_since_last_input() -> float:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), "GetLastInputInfo failed")
        # GetTickCount wraps every ~49.7 days; unsigned subtraction gives the right delta across a wrap.
        elapsed_ms = (_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return elapsed_ms / 1000.0

    def power_status() -> str:
        status = _SYSTEM_POWER_STATUS()
        if not _kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            raise OSError(ctypes.get_last_error(), "GetSystemPowerStatus failed")
        if status.ACLineStatus == _AC_ONLINE:
            return "ac"
        if status.ACLineStatus == _AC_OFFLINE:
            return "battery"
        return "unknown"

    class _FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    def _system_times() -> tuple[int, int]:
        """(idle, total) CPU time of the whole machine, in 100 ns units."""
        idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
        if not _kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise OSError(ctypes.get_last_error(), "GetSystemTimes failed")
        as_int = lambda t: (t.high << 32) | t.low  # noqa: E731
        return as_int(idle), as_int(kernel) + as_int(user)  # kernel time includes idle time

    def cpu_load_percent(sample_seconds: float = 2.0) -> float:
        """How busy the CPU is across all cores (0-100), measured over ``sample_seconds``."""
        idle0, total0 = _system_times()
        time.sleep(sample_seconds)
        idle1, total1 = _system_times()
        total = total1 - total0
        return 0.0 if total <= 0 else round(100.0 * (1 - (idle1 - idle0) / total), 1)

    def available_memory_mb() -> float:
        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError(ctypes.get_last_error(), "GlobalMemoryStatusEx failed")
        return status.ullAvailPhys / (1024 * 1024)

else:  # pragma: no cover - exercised only off Windows
    def seconds_since_last_input() -> float:
        # TODO(cross-platform): implement idle detection for Linux/macOS if the worker ever runs
        # there. On Windows this uses GetLastInputInfo. Until then, inject idle_fn on non-Windows.
        raise NotImplementedError("idle detection is only implemented on Windows; inject idle_fn")

    def cpu_load_percent(sample_seconds: float = 2.0) -> float:
        raise NotImplementedError("CPU load is only measured on Windows; inject load_fn")

    def available_memory_mb() -> float:
        raise NotImplementedError("free memory is only measured on Windows; inject memory_fn")

    def power_status() -> str:
        # TODO(cross-platform): implement AC detection for Linux/macOS (e.g. /sys/class/power_supply).
        raise NotImplementedError("power detection is only implemented on Windows; inject power_fn")


def check_host_ready(
    *,
    idle_threshold_seconds: float,
    require_ac: bool = True,
    unknown_power_is_ok: bool = True,
    idle_fn=seconds_since_last_input,
    power_fn=power_status,
) -> HostReadiness:
    """
    Return whether the host is ready to run the job: idle for at least ``idle_threshold_seconds`` and,
    if ``require_ac``, on AC power. ``idle_fn`` / ``power_fn`` are injectable for testing.

    A desktop with no battery reports AC; a laptop reports its real state. When power is "unknown",
    ``unknown_power_is_ok`` decides whether that blocks the run (default: allow).
    """
    seconds_idle = idle_fn()
    power = power_fn()
    idle = seconds_idle >= idle_threshold_seconds
    on_ac = power == "ac" or (power == "unknown" and unknown_power_is_ok)

    if not idle:
        reason = f"not idle ({seconds_idle:.0f}s < {idle_threshold_seconds:.0f}s)"
    elif require_ac and not on_ac:
        reason = f"not on AC power (power={power})"
    else:
        reason = "idle and on AC power"

    ready = idle and (on_ac or not require_ac)
    return HostReadiness(
        ready=ready, idle=idle, on_ac_power=on_ac, seconds_idle=round(seconds_idle, 1), power=power, reason=reason
    )


# The memory-writing model is the heaviest thing the job runs. Beyond "idle and on AC", it starts only when the machine is
# also lightly loaded right now: a render, a download being unpacked or a game left running keeps it waiting.
MAX_CPU_LOAD_FOR_MODEL = 35.0     # percent, all cores
MODEL_RAM_HEADROOM_MB = 1024.0    # free RAM needed beyond the model file itself


def check_light_load(
    model_bytes: int,
    *,
    load_fn=None,
    memory_fn=None,
    max_cpu_load: float = MAX_CPU_LOAD_FOR_MODEL,
) -> str | None:
    """``None`` when the PC is quiet enough to run the local model now, otherwise the reason it is not."""
    load = (load_fn or cpu_load_percent)()
    if load > max_cpu_load:
        return f"CPU busy ({load:.0f}% > {max_cpu_load:.0f}%)"
    free_mb = (memory_fn or available_memory_mb)()
    need_mb = model_bytes / (1024 * 1024) + MODEL_RAM_HEADROOM_MB
    if free_mb < need_mb:
        return f"not enough free memory ({free_mb:.0f} MB < {need_mb:.0f} MB)"
    return None

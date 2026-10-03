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

else:  # pragma: no cover - exercised only off Windows
    def seconds_since_last_input() -> float:
        # TODO(cross-platform): implement idle detection for Linux/macOS if the worker ever runs
        # there. On Windows this uses GetLastInputInfo. Until then, inject idle_fn on non-Windows.
        raise NotImplementedError("idle detection is only implemented on Windows; inject idle_fn")

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

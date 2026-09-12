"""Process RSS and peak-RSS readings from the OS, with no third-party dependency.

  * Windows: GetProcessMemoryInfo -> WorkingSetSize / PeakWorkingSetSize (psapi).
  * Linux:   /proc/self/statm for current RSS, getrusage(ru_maxrss) for peak.
  * macOS:   getrusage(ru_maxrss) for both (ru_maxrss is bytes on macOS, kilobytes on Linux).

"Peak" is the OS-maintained high-water mark since the process started (or since the last
``reset_peak_rss`` on Windows). Peak cannot be reset on POSIX, so ``reset_peak_rss`` is a no-op
there; run the measurement in a fresh process to get a clean peak.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # K32GetProcessMemoryInfo lives in kernel32, so psapi need not be loaded separately.
    _GetProcessMemoryInfo = _kernel32.K32GetProcessMemoryInfo
    _GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
    _GetProcessMemoryInfo.restype = wintypes.BOOL

    def _counters() -> _PROCESS_MEMORY_COUNTERS:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        handle = _kernel32.GetCurrentProcess()
        if not _GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return counters

    def current_rss_bytes() -> int:
        return int(_counters().WorkingSetSize)

    def peak_rss_bytes() -> int:
        return int(_counters().PeakWorkingSetSize)

    def reset_peak_rss() -> None:
        # Resets PeakWorkingSetSize to the current working set (Windows Vista+). Ignore failures.
        try:
            _kernel32.SetProcessWorkingSetSizeEx(
                _kernel32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1), wintypes.DWORD(0)
            )
        except Exception:
            pass

else:
    import resource

    _RU_MAXRSS_BYTES = sys.platform == "darwin"  # macOS reports bytes; Linux reports kilobytes

    def current_rss_bytes() -> int:
        try:
            with open("/proc/self/statm", "r", encoding="ascii") as f:
                resident_pages = int(f.read().split()[1])
            import os

            return resident_pages * os.sysconf("SC_PAGE_SIZE")
        except (FileNotFoundError, OSError, ValueError):
            return peak_rss_bytes()  # fall back to peak where /proc is unavailable (e.g. macOS)

    def peak_rss_bytes() -> int:
        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(maxrss if _RU_MAXRSS_BYTES else maxrss * 1024)

    def reset_peak_rss() -> None:
        # POSIX keeps a monotonic high-water mark that can't be reset; use a fresh process instead.
        return

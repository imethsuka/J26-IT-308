using CsharpCollector.Native;

namespace CsharpCollector.Capture;

internal static class DpiAwareness
{
    /// <summary>
    /// Must run before any capture. It returns false if awareness was already set (for example by
    /// a manifest), which is fine.
    /// </summary>
    public static bool EnablePerMonitorV2() =>
        NativeMethods.SetProcessDpiAwarenessContext(NativeMethods.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
}

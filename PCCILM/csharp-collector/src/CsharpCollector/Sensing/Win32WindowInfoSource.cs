using System.Diagnostics;
using System.Text;
using CsharpCollector.Native;

namespace CsharpCollector.Sensing;

internal sealed class Win32WindowInfoSource : IWindowInfoSource
{
    // UWP apps (Settings, Calculator, ...) all report "ApplicationFrameHost" here, so they
    // can only be excluded as a group.
    public string? GetProcessName(IntPtr hwnd)
    {
        NativeMethods.GetWindowThreadProcessId(hwnd, out var pid);
        if (pid == 0)
            return null;

        try
        {
            using var process = Process.GetProcessById((int)pid);
            return process.ProcessName;
        }
        catch (Exception ex) when (ex is ArgumentException or InvalidOperationException)
        {
            return null;
        }
    }

    public string GetTitle(IntPtr hwnd)
    {
        var length = NativeMethods.GetWindowTextLength(hwnd);
        if (length <= 0)
            return "";

        var buffer = new StringBuilder(length + 1);
        NativeMethods.GetWindowText(hwnd, buffer, buffer.Capacity);
        return buffer.ToString();
    }
}

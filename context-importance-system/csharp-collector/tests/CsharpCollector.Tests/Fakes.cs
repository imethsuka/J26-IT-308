using CsharpCollector.Events;
using CsharpCollector.Output;
using CsharpCollector.Sensing;

namespace CsharpCollector.Tests;

internal sealed record FakeWindow(string? Process, string Title, string? Url = null);

/// <summary>Stands in for the Win32 and UIA readers, and records every read so tests can assert what was touched.</summary>
internal sealed class FakeDesktop : IWindowInfoSource, IBrowserUrlReader
{
    private readonly Dictionary<IntPtr, FakeWindow> _windows = new();
    private int _nextHwnd = 1;

    public List<IntPtr> TitleReads { get; } = [];
    public List<IntPtr> UrlReads { get; } = [];

    public IntPtr Add(FakeWindow window)
    {
        var hwnd = new IntPtr(_nextHwnd++);
        _windows[hwnd] = window;
        return hwnd;
    }

    public void Update(IntPtr hwnd, FakeWindow window) => _windows[hwnd] = window;

    public string? GetProcessName(IntPtr hwnd) => _windows[hwnd].Process;

    public string GetTitle(IntPtr hwnd)
    {
        TitleReads.Add(hwnd);
        return _windows[hwnd].Title;
    }

    public string? TryReadUrl(IntPtr hwnd)
    {
        UrlReads.Add(hwnd);
        return _windows[hwnd].Url;
    }
}

internal sealed class RecordingSink : IEventSink
{
    public List<CollectorEvent> Events { get; } = [];

    public void Emit(CollectorEvent evt) => Events.Add(evt);
}

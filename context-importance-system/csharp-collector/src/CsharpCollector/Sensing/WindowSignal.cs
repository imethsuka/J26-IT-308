namespace CsharpCollector.Sensing;

internal enum WindowSignalKind
{
    Foreground,
    TitleChange,
}

/// <summary>
/// Raw notification from a hook callback: only "something changed on this hwnd". All reading
/// happens later in <see cref="WindowContextProcessor"/>, after the exclusion check.
/// </summary>
internal readonly record struct WindowSignal(WindowSignalKind Kind, IntPtr Hwnd, long Sequence = 0);

internal interface IWindowInfoSource
{
    /// <summary>Process name without ".exe", or null if it can't be determined.</summary>
    string? GetProcessName(IntPtr hwnd);

    string GetTitle(IntPtr hwnd);
}

internal interface IBrowserUrlReader
{
    /// <summary>Current address bar value, or null if it can't be read reliably.</summary>
    string? TryReadUrl(IntPtr hwnd);
}

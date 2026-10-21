namespace CsharpCollector.Content;

/// <summary>Rectangle in physical screen pixels (the process is per-monitor DPI aware).</summary>
internal readonly record struct ScreenRect(int X, int Y, int Width, int Height)
{
    public bool IsEmpty => Width <= 0 || Height <= 0;

    public ScreenRect Intersect(ScreenRect other)
    {
        var left = Math.Max(X, other.X);
        var top = Math.Max(Y, other.Y);
        var right = Math.Min(X + Width, other.X + other.Width);
        var bottom = Math.Min(Y + Height, other.Y + other.Height);
        return right > left && bottom > top ? new ScreenRect(left, top, right - left, bottom - top) : default;
    }
}

/// <summary>A large picture on screen (product photo, chart, diagram): where it is and its alt text.</summary>
internal sealed record ContentImage(ScreenRect Bounds, string? AltText);

/// <summary>What the foreground window shows: its visible text and its large images.</summary>
internal sealed record ScreenContent(
    string Source,
    string Text,
    IReadOnlyList<ContentImage> Images,
    bool Truncated,
    int NodesVisited,
    double ReadMs)
{
    public const string Uia = "uia";
}

internal sealed record ContentLimits(
    int MaxChars = 50_000,
    int MaxNodes = 3_000,
    int TimeBudgetMs = 400,
    int MinImageSide = 64);

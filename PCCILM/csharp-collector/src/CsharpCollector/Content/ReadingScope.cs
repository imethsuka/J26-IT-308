namespace CsharpCollector.Content;

/// <summary>
/// Which part of a window to read. Most windows are read whole. But an app can put a second document beside its main one
/// (VS Code's chat and tool panes are webviews; browsers have side panels), and the whole-window text then mixes two
/// activities: on the author's PC, memories titled after a file in VS Code described the chat beside it. When the keyboard
/// focus is inside such a nested document, only that document is read.
/// </summary>
internal static class ReadingScope
{
    /// <summary>Ancestors walked up from the focus before giving up (VS Code's chat input is ~20 levels deep).</summary>
    public const int MaxDepth = 60;

    /// <summary>A document smaller than this share of the window is a pane of its own; a browser page fills ~90%.</summary>
    public const double MaxPaneShare = 0.7;

    /// <summary>
    /// <paramref name="focusToWindow"/>: the focused element and its ancestors, innermost first, up to (not including) the
    /// window. Returns the index of the outermost document that still holds the focus and is clearly smaller than the window
    /// (the whole pane, not a nested frame inside it), or null to read the whole window.
    /// </summary>
    public static int? Choose(ScreenRect window, IReadOnlyList<(bool IsDocument, ScreenRect Bounds)> focusToWindow)
    {
        var windowArea = Area(window);
        if (windowArea <= 0) return null;
        int? pane = null;
        for (var i = 0; i < focusToWindow.Count; i++)
        {
            var (isDocument, bounds) = focusToWindow[i];
            if (!isDocument || Area(bounds) <= 0) continue;
            if (Area(bounds) < MaxPaneShare * windowArea) pane = i;  // keep going: an outer document of the same pane wins
            else break;                                               // reached the window's main document
        }
        return pane;
    }

    private static double Area(ScreenRect r) => r.IsEmpty ? 0 : (double)r.Width * r.Height;
}

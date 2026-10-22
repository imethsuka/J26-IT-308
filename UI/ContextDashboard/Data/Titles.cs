using System.Text.RegularExpressions;

namespace ContextDashboard.Data;

/// <summary>
/// A window title as a person would name the page. Mirrors <c>display_title</c> in <c>python-worker/features/titles.py</c>
/// (which the model's prompts use): invisible characters, notification counters, unsaved markers and Edge's
/// "and N more pages" go, and so does the trailing browser/editor name with Edge's profile.
/// "(3) Inbox and 2 more pages - Personal - Microsoft​ Edge" becomes "Inbox".
/// </summary>
public static partial class Titles
{
    public static string Display(string? title)
    {
        var text = ZeroWidth().Replace(title ?? "", "");
        string previous;
        do
        {
            previous = text;
            text = LeadingUnsaved().Replace(LeadingCounter().Replace(text, ""), "");
        } while (text != previous);
        text = Spaces().Replace(MorePages().Replace(text, ""), " ").Trim();
        var bare = AppSuffix().Replace(text, "").Trim();
        return bare.Length > 0 ? bare : text;
    }

    [GeneratedRegex("[​-‏⁠﻿]")] private static partial Regex ZeroWidth();
    [GeneratedRegex(@"^\s*[(\[]\d+\+?[)\]]\s*")] private static partial Regex LeadingCounter();
    [GeneratedRegex(@"^\s*[●•◉*]\s*")] private static partial Regex LeadingUnsaved();
    [GeneratedRegex(@"\s+and \d+ more pages?(?=\s+-\s|\s*$)")] private static partial Regex MorePages();
    [GeneratedRegex(@"\s+")] private static partial Regex Spaces();
    [GeneratedRegex(@"(\s+[-–—]\s+(Personal|Work|Profile \d+))?\s+[-–—]\s+(Microsoft\s*Edge|Google Chrome|Mozilla Firefox|Zen Browser|Brave|Opera|Visual Studio Code)\s*$")]
    private static partial Regex AppSuffix();
}

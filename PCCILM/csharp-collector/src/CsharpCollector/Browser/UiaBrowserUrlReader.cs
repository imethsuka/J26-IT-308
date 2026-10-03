using System.Runtime.InteropServices;
using System.Windows.Automation;
using CsharpCollector.Sensing;

namespace CsharpCollector.Browser;

/// <summary>
/// Reads the Edge/Chrome address bar through UI Automation. It must not run on the hook thread,
/// because UIA calls are cross-process and slow enough to trip the low-level hook timeout.
/// </summary>
internal sealed class UiaBrowserUrlReader : IBrowserUrlReader
{
    private const int MaxNodesVisited = 1000;
    private const int MaxCachedWindows = 32;

    private readonly Dictionary<IntPtr, AutomationElement> _addressBars = new();

    public string? TryReadUrl(IntPtr hwnd)
    {
        for (var attempt = 0; attempt < 2; attempt++)
        {
            try
            {
                if (!_addressBars.TryGetValue(hwnd, out var bar))
                {
                    bar = FindAddressBar(hwnd);
                    if (bar is null)
                        return null;
                    if (_addressBars.Count >= MaxCachedWindows)
                        _addressBars.Clear();
                    _addressBars[hwnd] = bar;
                }

                // While the address bar has focus it holds whatever the user is typing, which is
                // keyboard content. Report "unknown" instead.
                if (bar.Current.HasKeyboardFocus)
                    return null;

                var value = ((ValuePattern)bar.GetCurrentPattern(ValuePattern.Pattern)).Current.Value;
                return string.IsNullOrWhiteSpace(value) ? null : value;
            }
            catch (Exception ex) when (ex is ElementNotAvailableException or COMException or InvalidOperationException)
            {
                _addressBars.Remove(hwnd);
            }
        }
        return null;
    }

    // Depth-first search in tree order for the first editable text field outside web content.
    // Page content sits under Document nodes, and we never descend into them, so a page's own
    // input fields can't be mistaken for the address bar.
    private static AutomationElement? FindAddressBar(IntPtr hwnd)
    {
        var walker = TreeWalker.ControlViewWalker;
        var stack = new Stack<AutomationElement>();
        stack.Push(AutomationElement.FromHandle(hwnd));
        var visited = 0;

        while (stack.Count > 0 && visited++ < MaxNodesVisited)
        {
            var node = stack.Pop();
            var type = node.Current.ControlType;

            if (type == ControlType.Document)
                continue;

            if (type == ControlType.Edit &&
                (bool)node.GetCurrentPropertyValue(AutomationElement.IsValuePatternAvailableProperty))
                return node;

            var children = new List<AutomationElement>();
            for (var child = walker.GetFirstChild(node); child is not null; child = walker.GetNextSibling(child))
                children.Add(child);
            for (var i = children.Count - 1; i >= 0; i--)
                stack.Push(children[i]);
        }
        return null;
    }
}

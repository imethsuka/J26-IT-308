using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Automation;

namespace CsharpCollector.Content;

/// <summary>
/// <see cref="IContentNode"/> over UI Automation. Every UIA property read is a cross-process call,
/// so each node's properties are fetched in one batch (a <see cref="CacheRequest"/>) when its
/// parent lists its children; only Value and visible text are read live, and only on the few
/// nodes that need them.
/// </summary>
internal sealed class UiaContentNode : IContentNode
{
    private static readonly CacheRequest Request = BuildRequest();

    private readonly AutomationElement _element;

    private UiaContentNode(AutomationElement element) => _element = element;

    public static UiaContentNode FromHandle(IntPtr hwnd)
    {
        using (Request.Activate())
            return new UiaContentNode(AutomationElement.FromHandle(hwnd));
    }

    /// <summary>
    /// The panel the user is working in, when it is a document of its own that is clearly smaller than the window (a chat
    /// or tool pane beside an editor, a browser side panel), or null to read the whole window. See <see cref="ReadingScope"/>.
    /// </summary>
    public static UiaContentNode? FocusedPane(IntPtr hwnd, ScreenRect window)
    {
        var focused = AutomationElement.FocusedElement;
        var walker = TreeWalker.ControlViewWalker;
        var chain = new List<(bool IsDocument, ScreenRect Bounds, AutomationElement Element)>();
        var inWindow = false;
        for (var e = focused; e is not null && chain.Count < ReadingScope.MaxDepth; e = walker.GetParent(e))
        {
            var current = e.Current;
            if (current.NativeWindowHandle == hwnd.ToInt32())
            {
                inWindow = true; // the focus is inside the window being read, not some other app's popup
                break;
            }
            chain.Add((current.ControlType == ControlType.Document, ToRect(current.BoundingRectangle), e));
        }
        if (!inWindow) return null;
        var index = ReadingScope.Choose(window, chain.Select(c => (c.IsDocument, c.Bounds)).ToList());
        if (index is not { } i) return null;
        using (Request.Activate())
            return new UiaContentNode(chain[i].Element.GetUpdatedCache(Request));
    }

    private static ScreenRect ToRect(System.Windows.Rect r) =>
        r.IsEmpty || double.IsInfinity(r.Width) ? default : new ScreenRect((int)r.X, (int)r.Y, (int)r.Width, (int)r.Height);

    public ContentKind Kind => KindOf(_element.Cached.ControlType);
    public string? Name => _element.Cached.Name;
    public bool IsOffscreen => _element.Cached.IsOffscreen;
    public bool IsPassword => _element.Cached.IsPassword;
    public bool HasKeyboardFocus => _element.Cached.HasKeyboardFocus;

    public ScreenRect Bounds => ToRect(_element.Cached.BoundingRectangle);

    public string? TryGetValue()
    {
        if (!(bool)_element.GetCachedPropertyValue(AutomationElement.IsValuePatternAvailableProperty))
            return null;
        return ((ValuePattern)_element.GetCurrentPattern(ValuePattern.Pattern)).Current.Value;
    }

    public string? TryGetVisibleText(int maxChars)
    {
        if (maxChars <= 0 || !(bool)_element.GetCachedPropertyValue(AutomationElement.IsTextPatternAvailableProperty))
            return null;
        var pattern = (TextPattern)_element.GetCurrentPattern(TextPattern.Pattern);
        var text = new StringBuilder();
        foreach (var range in pattern.GetVisibleRanges())
        {
            if (text.Length >= maxChars)
                break;
            if (text.Length > 0)
                text.Append('\n');
            text.Append(range.GetText(maxChars - text.Length));
        }
        return text.ToString();
    }

    // On-screen Text / Edit / Image nodes only. Hyperlinks and list items aren't requested: on web
    // pages their names repeat the Text nodes inside them.
    private static readonly Condition VisibleContentCondition = new AndCondition(
        Automation.ControlViewCondition,
        new PropertyCondition(AutomationElement.IsOffscreenProperty, false),
        new OrCondition(
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Text),
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Edit),
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Image)));

    public IReadOnlyList<IContentNode>? FindVisibleContent()
    {
        using (Request.Activate())
        {
            var found = _element.FindAll(TreeScope.Descendants, VisibleContentCondition);
            var nodes = new IContentNode[found.Count];
            for (var i = 0; i < found.Count; i++)
                nodes[i] = new UiaContentNode(found[i]);
            return nodes;
        }
    }

    public IReadOnlyList<IContentNode> GetChildren()
    {
        using (Request.Activate())
        {
            var children = _element.FindAll(TreeScope.Children, Automation.ControlViewCondition);
            var nodes = new IContentNode[children.Count];
            for (var i = 0; i < children.Count; i++)
                nodes[i] = new UiaContentNode(children[i]);
            return nodes;
        }
    }

    private static ContentKind KindOf(ControlType type)
    {
        if (type == ControlType.Document) return ContentKind.Document;
        if (type == ControlType.Text) return ContentKind.Text;
        if (type == ControlType.Edit) return ContentKind.Edit;
        if (type == ControlType.Hyperlink) return ContentKind.Hyperlink;
        if (type == ControlType.ListItem) return ContentKind.ListItem;
        if (type == ControlType.DataItem) return ContentKind.DataItem;
        if (type == ControlType.TreeItem) return ContentKind.TreeItem;
        if (type == ControlType.Image) return ContentKind.Image;
        // Menus, toolbars (incl. the browser's address and bookmarks bars), the title bar, tabs
        // (in browsers: the titles of *other* pages) and scroll/status bars are window chrome.
        if (type == ControlType.MenuBar || type == ControlType.Menu || type == ControlType.ToolBar ||
            type == ControlType.TitleBar || type == ControlType.TabItem || type == ControlType.ScrollBar ||
            type == ControlType.StatusBar)
            return ContentKind.Chrome;
        return ContentKind.Other;
    }

    private static CacheRequest BuildRequest()
    {
        var request = new CacheRequest { TreeFilter = Automation.ControlViewCondition };
        request.Add(AutomationElement.ControlTypeProperty);
        request.Add(AutomationElement.NameProperty);
        request.Add(AutomationElement.BoundingRectangleProperty);
        request.Add(AutomationElement.IsOffscreenProperty);
        request.Add(AutomationElement.IsPasswordProperty);
        request.Add(AutomationElement.HasKeyboardFocusProperty);
        request.Add(AutomationElement.IsValuePatternAvailableProperty);
        request.Add(AutomationElement.IsTextPatternAvailableProperty);
        return request;
    }
}

/// <summary>
/// Reads the screen content of a window through UI Automation, within <see cref="ContentLimits"/>.
/// Like the URL reader, it must never run on the hook thread (UIA calls are cross-process). The
/// caller checks exclusions first and re-checks the context gate afterwards.
/// </summary>
internal sealed class UiaScreenContentReader : IScreenContentReader
{
    private readonly ContentLimits _limits;

    public UiaScreenContentReader(ContentLimits limits) => _limits = limits;

    /// <summary>The focused pane, or null (read the whole window) when there is none or it can't be found.</summary>
    private static UiaContentNode? TryFocusedPane(IntPtr hwnd, ScreenRect window)
    {
        try
        {
            return UiaContentNode.FocusedPane(hwnd, window);
        }
        catch (Exception ex) when (ex is ElementNotAvailableException or COMException or InvalidOperationException or ArgumentException)
        {
            return null;
        }
    }

    /// <summary>The window's content, or null if UIA couldn't read it (window closed, app hung, ...).
    /// A page that is re-rendering can invalidate an element mid-read, so a failed read is retried once.</summary>
    public ScreenContent? TryRead(IntPtr hwnd)
    {
        for (var attempt = 0; attempt < 2; attempt++)
        {
            var start = System.Diagnostics.Stopwatch.GetTimestamp();
            try
            {
                var window = UiaContentNode.FromHandle(hwnd);
                var root = TryFocusedPane(hwnd, window.Bounds) ?? window;
                return ScreenContentExtractor.Extract(root, _limits, () => System.Diagnostics.Stopwatch.GetElapsedTime(start));
            }
            catch (Exception ex) when (ex is ElementNotAvailableException or COMException or InvalidOperationException or ArgumentException)
            {
            }
        }
        return null;
    }
}

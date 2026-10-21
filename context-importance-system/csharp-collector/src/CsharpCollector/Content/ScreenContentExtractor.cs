using System.Text;
using System.Text.RegularExpressions;

namespace CsharpCollector.Content;

/// <summary>What the extractor needs to know about one accessibility node.</summary>
internal enum ContentKind
{
    /// <summary>Containers and controls with no content of their own (panes, groups, buttons): walked, not read.</summary>
    Other,
    Document,
    Text,
    Edit,
    Hyperlink,
    ListItem,
    DataItem,
    TreeItem,
    Image,
    /// <summary>Window chrome (menus, toolbars, title bar, tabs, scroll and status bars): neither read nor walked.</summary>
    Chrome,
}

/// <summary>
/// One node of a window's accessibility tree. <see cref="UiaContentNode"/> wraps UI Automation;
/// tests use a fake tree.
/// </summary>
internal interface IContentNode
{
    ContentKind Kind { get; }
    string? Name { get; }
    ScreenRect Bounds { get; }
    bool IsOffscreen { get; }
    bool IsPassword { get; }
    bool HasKeyboardFocus { get; }

    /// <summary>The control's value (ValuePattern), or null if it has none.</summary>
    string? TryGetValue();

    /// <summary>Text currently scrolled into view (TextPattern visible ranges), or null if unsupported.
    /// Can be slow (seconds on a long Edge page); used only as a fallback.</summary>
    string? TryGetVisibleText(int maxChars);

    /// <summary>
    /// Every on-screen Text, Edit and Image node below this one, in document order, fetched in one
    /// bulk request; or null if bulk search isn't supported. One request instead of one per node
    /// is what makes reading a web page fast.
    /// </summary>
    IReadOnlyList<IContentNode>? FindVisibleContent();

    IReadOnlyList<IContentNode> GetChildren();
}

/// <summary>
/// Walks a window's accessibility tree and collects what the user can see: text, and large images
/// with their alt text. Privacy rules, applied while walking:
/// <list type="bullet">
/// <item>password fields are never read, nor anything inside them;</item>
/// <item>an edit field with keyboard focus isn't read: it holds what the user is typing right now
/// (the same rule as the address bar in <c>UiaBrowserUrlReader</c>);</item>
/// <item>off-screen nodes are skipped, so only what is actually visible is collected.</item>
/// </list>
/// The exclusion check happens before this is called (see <c>UiaScreenContentReader</c>).
/// </summary>
internal static class ScreenContentExtractor
{
    // Short strings that repeat (a list item and its text child both expose the same name, or the
    // same "Reply" link appears many times) are kept once. Longer text is always kept.
    private const int DedupMaxLength = 200;

    public static ScreenContent Extract(IContentNode root, ContentLimits limits, Func<TimeSpan> elapsed)
    {
        var window = root.Bounds;
        var text = new StringBuilder();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var images = new List<ContentImage>();
        var nodes = 0;
        var truncated = false;
        var textFull = false;

        var stack = new Stack<(IContentNode Node, bool InsideReadDocument)>();
        stack.Push((root, false));
        while (stack.Count > 0)
        {
            if (nodes >= limits.MaxNodes || elapsed().TotalMilliseconds > limits.TimeBudgetMs)
            {
                truncated = true;
                break;
            }
            var (node, insideReadDocument) = stack.Pop();
            nodes++;

            if (node.Kind == ContentKind.Chrome || node.IsPassword || (node.IsOffscreen && !ReferenceEquals(node, root)))
                continue;

            var readDocument = insideReadDocument;
            switch (node.Kind)
            {
                case ContentKind.Image:
                    AddImage(node);
                    continue;  // an image's children are its own decoration

                case ContentKind.Document when !insideReadDocument:
                    // Fast path: everything visible in the document in one bulk request, then done
                    // with this subtree.
                    if (node.FindVisibleContent() is { } found)
                    {
                        var textBefore = text.Length;
                        foreach (var leaf in found)
                        {
                            if (nodes >= limits.MaxNodes)
                            {
                                truncated = true;
                                break;
                            }
                            nodes++;
                            AddLeaf(leaf);
                        }
                        // Some documents (rich-text editors) expose their text only through the
                        // text pattern. Use it if the bulk request found none and time is left.
                        if (text.Length == textBefore && !textFull && elapsed().TotalMilliseconds < limits.TimeBudgetMs)
                            Add(node.TryGetVisibleText(limits.MaxChars - text.Length));
                        continue;
                    }
                    if (textFull)
                        break;
                    var visible = node.TryGetVisibleText(limits.MaxChars - text.Length);
                    if (visible is not null)
                    {
                        Add(visible);
                        // The visible text already covers everything inside this document; below
                        // it, only images are still collected.
                        readDocument = true;
                    }
                    break;

                case ContentKind.Text or ContentKind.Hyperlink or ContentKind.ListItem
                    or ContentKind.DataItem or ContentKind.TreeItem when !insideReadDocument:
                    Add(node.Name);
                    break;

                case ContentKind.Edit when !insideReadDocument && !node.HasKeyboardFocus:
                    Add(node.TryGetValue());
                    break;
            }

            var children = node.GetChildren();
            for (var i = children.Count - 1; i >= 0; i--)
                stack.Push((children[i], readDocument));
        }

        return new ScreenContent(ScreenContent.Uia, text.ToString(), images, truncated, nodes, elapsed().TotalMilliseconds);

        // A node from a bulk request: no subtree walk, the same privacy rules.
        void AddLeaf(IContentNode leaf)
        {
            if (leaf.IsPassword || leaf.IsOffscreen)
                return;
            switch (leaf.Kind)
            {
                case ContentKind.Image:
                    AddImage(leaf);
                    break;
                case ContentKind.Edit when !leaf.HasKeyboardFocus:
                    Add(leaf.TryGetValue());
                    break;
                case ContentKind.Text or ContentKind.Hyperlink or ContentKind.ListItem
                    or ContentKind.DataItem or ContentKind.TreeItem:
                    Add(leaf.Name);
                    break;
            }
        }

        void AddImage(IContentNode node)
        {
            var bounds = window.IsEmpty ? node.Bounds : node.Bounds.Intersect(window);
            if (bounds.Width >= limits.MinImageSide && bounds.Height >= limits.MinImageSide)
                images.Add(new ContentImage(bounds, NullIfEmpty(Clean(node.Name))));
        }

        void Add(string? raw)
        {
            if (textFull)
                return;
            var piece = Clean(raw);
            if (piece.Length == 0 || (piece.Length <= DedupMaxLength && !seen.Add(piece)))
                return;

            var room = limits.MaxChars - text.Length - (text.Length > 0 ? 1 : 0);
            if (piece.Length > room)
            {
                piece = room > 0 ? piece[..room] : "";
                textFull = truncated = true;
            }
            if (piece.Length == 0)
                return;
            if (text.Length > 0)
                text.Append('\n');
            text.Append(piece);
        }
    }

    private static readonly Regex ZeroWidth = new("[​-‏⁠﻿]", RegexOptions.Compiled);
    private static readonly Regex Spaces = new(@"[ \t ]+", RegexOptions.Compiled);
    private static readonly Regex BlankLines = new(@"\s*\n\s*", RegexOptions.Compiled);

    /// <summary>Invisible characters removed, runs of spaces collapsed, line breaks kept (one per break).</summary>
    internal static string Clean(string? raw)
    {
        if (string.IsNullOrEmpty(raw))
            return "";
        var text = ZeroWidth.Replace(raw.Replace("\r\n", "\n").Replace('\r', '\n'), "");
        text = Spaces.Replace(text, " ");
        return BlankLines.Replace(text, "\n").Trim();
    }

    private static string? NullIfEmpty(string value) => value.Length == 0 ? null : value;
}

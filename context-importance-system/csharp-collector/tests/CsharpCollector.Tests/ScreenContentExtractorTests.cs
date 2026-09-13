using CsharpCollector.Content;

namespace CsharpCollector.Tests;

internal sealed class FakeNode : IContentNode
{
    public FakeNode(ContentKind kind, string? name = null, params FakeNode[] children)
    {
        Kind = kind;
        Name = name;
        Children = children.ToList();
    }

    public ContentKind Kind { get; }
    public string? Name { get; init; }
    public ScreenRect Bounds { get; init; } = new(0, 0, 100, 20);
    public bool IsOffscreen { get; init; }
    public bool IsPassword { get; init; }
    public bool HasKeyboardFocus { get; init; }
    public string? Value { get; init; }
    public string? VisibleText { get; init; }
    public List<FakeNode>? Found { get; init; }
    public List<FakeNode> Children { get; }
    public int VisibleTextReads { get; private set; }

    public int ValueReads { get; private set; }
    public int ChildReads { get; private set; }

    public string? TryGetValue()
    {
        ValueReads++;
        return Value;
    }

    public string? TryGetVisibleText(int maxChars)
    {
        VisibleTextReads++;
        return VisibleText is null ? null : VisibleText[..Math.Min(maxChars, VisibleText.Length)];
    }

    public IReadOnlyList<IContentNode>? FindVisibleContent() => Found;

    public IReadOnlyList<IContentNode> GetChildren()
    {
        ChildReads++;
        return Children;
    }
}

public class ScreenContentExtractorTests
{
    private static readonly ScreenRect Window = new(0, 0, 1920, 1080);

    private static FakeNode Root(params FakeNode[] children) =>
        new(ContentKind.Other, "window", children) { Bounds = Window };

    private static ScreenContent Extract(FakeNode root, ContentLimits? limits = null) =>
        ScreenContentExtractor.Extract(root, limits ?? new ContentLimits(), () => TimeSpan.Zero);

    [Fact]
    public void Collects_text_in_reading_order()
    {
        var content = Extract(Root(
            new FakeNode(ContentKind.Text, "Hello"),
            new FakeNode(ContentKind.Other, null,
                new FakeNode(ContentKind.Hyperlink, "Read more"),
                new FakeNode(ContentKind.ListItem, "notes.txt")),
            new FakeNode(ContentKind.Text, "Bye")));

        Assert.Equal("Hello\nRead more\nnotes.txt\nBye", content.Text);
        Assert.False(content.Truncated);
        Assert.Equal(ScreenContent.Uia, content.Source);
    }

    [Fact]
    public void Document_visible_text_is_used_once_and_its_children_give_only_images()
    {
        var doc = new FakeNode(ContentKind.Document, "page",
            new FakeNode(ContentKind.Text, "Lorem ipsum"),
            new FakeNode(ContentKind.Image, "Red Oxford cotton shirt") { Bounds = new(420, 180, 600, 600) })
        {
            VisibleText = "Lorem ipsum dolor sit amet",
        };

        var content = Extract(Root(doc));

        Assert.Equal("Lorem ipsum dolor sit amet", content.Text);
        var image = Assert.Single(content.Images);
        Assert.Equal(new ScreenRect(420, 180, 600, 600), image.Bounds);
        Assert.Equal("Red Oxford cotton shirt", image.AltText);
    }

    [Fact]
    public void Document_bulk_search_is_used_instead_of_walking_or_the_slow_text_pattern()
    {
        var walkedChild = new FakeNode(ContentKind.Text, "never walked");
        var doc = new FakeNode(ContentKind.Document, "page", walkedChild)
        {
            VisibleText = "slow text pattern",
            Found =
            [
                new FakeNode(ContentKind.Text, "Oxford Cotton Shirt"),
                new FakeNode(ContentKind.Image, "Red Oxford cotton shirt") { Bounds = new(420, 180, 600, 600) },
                new FakeNode(ContentKind.Text, "LKR 4,990"),
                new FakeNode(ContentKind.Edit) { Value = "M" },
            ],
        };

        var content = Extract(Root(doc));

        Assert.Equal("Oxford Cotton Shirt\nLKR 4,990\nM", content.Text);
        Assert.Equal("Red Oxford cotton shirt", Assert.Single(content.Images).AltText);
        Assert.Equal(0, doc.ChildReads);
        Assert.Equal(0, doc.VisibleTextReads);
    }

    [Fact]
    public void Bulk_results_follow_the_same_privacy_rules()
    {
        var password = new FakeNode(ContentKind.Edit) { IsPassword = true, Value = "hunter2" };
        var typing = new FakeNode(ContentKind.Edit) { HasKeyboardFocus = true, Value = "half-typed" };
        var doc = new FakeNode(ContentKind.Document)
        {
            Found = [password, typing, new FakeNode(ContentKind.Text, "gone") { IsOffscreen = true }, new FakeNode(ContentKind.Text, "Sign in")],
        };

        var content = Extract(Root(doc));

        Assert.Equal("Sign in", content.Text);
        Assert.Equal(0, password.ValueReads);
        Assert.Equal(0, typing.ValueReads);
    }

    [Fact]
    public void Text_pattern_is_the_fallback_when_bulk_search_finds_no_text()
    {
        var doc = new FakeNode(ContentKind.Document)
        {
            VisibleText = "rich edit text",
            Found = [new FakeNode(ContentKind.Image, "logo") { Bounds = new(0, 0, 200, 200) }],
        };

        var content = Extract(Root(doc));

        Assert.Equal("rich edit text", content.Text);
        Assert.Single(content.Images);
    }

    [Fact]
    public void Bulk_results_count_toward_the_node_limit()
    {
        var doc = new FakeNode(ContentKind.Document)
        {
            Found = Enumerable.Range(0, 50).Select(i => new FakeNode(ContentKind.Text, $"line {i}")).ToList(),
        };

        var content = Extract(Root(doc), new ContentLimits(MaxNodes: 12));

        Assert.True(content.Truncated);
        Assert.Equal(12, content.NodesVisited);
    }

    [Fact]
    public void Document_without_text_pattern_falls_back_to_its_children()
    {
        var doc = new FakeNode(ContentKind.Document, "page", new FakeNode(ContentKind.Text, "child text"));
        Assert.Equal("child text", Extract(Root(doc)).Text);
    }

    [Fact]
    public void Password_fields_and_their_subtrees_are_never_read()
    {
        var password = new FakeNode(ContentKind.Edit, "Password", new FakeNode(ContentKind.Text, "hunter2"))
        {
            IsPassword = true,
            Value = "hunter2",
        };

        var content = Extract(Root(password, new FakeNode(ContentKind.Text, "Sign in")));

        Assert.Equal("Sign in", content.Text);
        Assert.Equal(0, password.ValueReads);
        Assert.Equal(0, password.ChildReads);
    }

    [Fact]
    public void Edit_being_typed_into_is_not_read_but_other_edits_are()
    {
        var typing = new FakeNode(ContentKind.Edit) { HasKeyboardFocus = true, Value = "half-typed message" };
        var other = new FakeNode(ContentKind.Edit) { Value = "saved draft" };

        var content = Extract(Root(typing, other));

        Assert.Equal("saved draft", content.Text);
        Assert.Equal(0, typing.ValueReads);
    }

    [Fact]
    public void Offscreen_nodes_and_window_chrome_are_skipped_without_walking_them()
    {
        var offscreen = new FakeNode(ContentKind.Other, null, new FakeNode(ContentKind.Text, "scrolled away")) { IsOffscreen = true };
        var tabs = new FakeNode(ContentKind.Chrome, "other tab title", new FakeNode(ContentKind.Text, "tab text"));

        var content = Extract(Root(offscreen, tabs, new FakeNode(ContentKind.Text, "visible")));

        Assert.Equal("visible", content.Text);
        Assert.Equal(0, offscreen.ChildReads);
        Assert.Equal(0, tabs.ChildReads);
    }

    [Fact]
    public void Small_images_are_ignored_and_large_ones_clipped_to_the_window()
    {
        var content = Extract(Root(
            new FakeNode(ContentKind.Image, "icon") { Bounds = new(10, 10, 32, 32) },
            new FakeNode(ContentKind.Image, "") { Bounds = new(1800, 900, 400, 400) }));

        var image = Assert.Single(content.Images);
        Assert.Equal(new ScreenRect(1800, 900, 120, 180), image.Bounds);
        Assert.Null(image.AltText);
    }

    [Fact]
    public void Short_repeated_strings_are_kept_once_long_text_always()
    {
        var longText = new string('x', 250);
        var content = Extract(Root(
            new FakeNode(ContentKind.ListItem, "Reply"),
            new FakeNode(ContentKind.Text, "Reply"),
            new FakeNode(ContentKind.Text, longText),
            new FakeNode(ContentKind.Text, longText)));

        Assert.Equal($"Reply\n{longText}\n{longText}", content.Text);
    }

    [Fact]
    public void Text_is_cut_at_max_chars_and_marked_truncated()
    {
        var content = Extract(
            Root(new FakeNode(ContentKind.Text, "abcdef"), new FakeNode(ContentKind.Text, "ghijkl")),
            new ContentLimits(MaxChars: 9));

        Assert.Equal("abcdef\ngh", content.Text);
        Assert.True(content.Truncated);
    }

    [Fact]
    public void Node_limit_stops_the_walk()
    {
        var children = Enumerable.Range(0, 50).Select(i => new FakeNode(ContentKind.Text, $"item {i}")).ToArray();
        var content = Extract(Root(children), new ContentLimits(MaxNodes: 10));

        Assert.True(content.Truncated);
        Assert.Equal(10, content.NodesVisited);
        Assert.Equal(9, content.Text.Split('\n').Length);  // the root + 9 items
    }

    [Fact]
    public void Time_budget_stops_the_walk()
    {
        var calls = 0;
        var children = Enumerable.Range(0, 50).Select(i => new FakeNode(ContentKind.Text, $"item {i}")).ToArray();
        var content = ScreenContentExtractor.Extract(
            Root(children), new ContentLimits(TimeBudgetMs: 100), () => TimeSpan.FromMilliseconds(calls++ < 5 ? 0 : 500));

        Assert.True(content.Truncated);
        Assert.True(content.NodesVisited < 10);
    }

    [Theory]
    [InlineData("a​b", "ab")]
    [InlineData("  many   spaces\there ", "many spaces here")]
    [InlineData("line one\r\n\r\n\n  line two", "line one\nline two")]
    [InlineData(null, "")]
    public void Clean_normalizes_text(string? raw, string expected) =>
        Assert.Equal(expected, ScreenContentExtractor.Clean(raw));

    [Fact]
    public void Rect_intersection()
    {
        Assert.Equal(new ScreenRect(5, 5, 5, 5), new ScreenRect(0, 0, 10, 10).Intersect(new ScreenRect(5, 5, 10, 10)));
        Assert.True(new ScreenRect(0, 0, 10, 10).Intersect(new ScreenRect(20, 20, 5, 5)).IsEmpty);
    }
}

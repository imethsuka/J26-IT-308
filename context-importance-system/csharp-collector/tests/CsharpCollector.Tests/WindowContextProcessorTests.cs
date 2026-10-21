using CsharpCollector.Events;
using CsharpCollector.Filtering;
using CsharpCollector.Sensing;

namespace CsharpCollector.Tests;

/// <summary>
/// Feeds mocked hook signals through the processor and checks that the exclusion filter runs
/// before anything else. Excluded contexts must never have their title or URL read.
/// </summary>
public class WindowContextProcessorTests
{
    private readonly FakeDesktop _desktop = new();
    private readonly RecordingSink _sink = new();

    private WindowContextProcessor Processor(string[]? apps = null, string[]? domains = null) =>
        new(new ExclusionFilter(apps ?? [], domains ?? []), _desktop, _desktop, ["chrome", "msedge"], _sink);

    private static WindowSignal Foreground(IntPtr hwnd) => new(WindowSignalKind.Foreground, hwnd);
    private static WindowSignal TitleChange(IntPtr hwnd) => new(WindowSignalKind.TitleChange, hwnd);

    [Fact]
    public void Excluded_app_is_never_read_and_emits_only_a_marker()
    {
        var hwnd = _desktop.Add(new FakeWindow("KeePass", "Passwords.kdbx - KeePass"));

        var allowed = Processor(apps: ["keepass.exe"]).Process(Foreground(hwnd));

        Assert.False(allowed);
        Assert.Empty(_desktop.TitleReads);
        Assert.Empty(_desktop.UrlReads);
        var evt = Assert.Single(_sink.Events);
        Assert.IsType<ExcludedContextEvent>(evt);
    }

    [Fact]
    public void Excluded_browser_process_never_has_its_url_read()
    {
        var hwnd = _desktop.Add(new FakeWindow("msedge", "Some page", "https://example.com"));

        Processor(apps: ["msedge"]).Process(Foreground(hwnd));

        Assert.Empty(_desktop.UrlReads);
        Assert.Empty(_desktop.TitleReads);
    }

    [Fact]
    public void Excluded_domain_suppresses_title_and_url()
    {
        var hwnd = _desktop.Add(new FakeWindow("chrome", "My Bank - Accounts", "https://secure.bank.com/accounts"));

        var allowed = Processor(domains: ["bank.com"]).Process(Foreground(hwnd));

        Assert.False(allowed);
        Assert.Empty(_desktop.TitleReads);
        Assert.IsType<ExcludedContextEvent>(Assert.Single(_sink.Events));
    }

    [Fact]
    public void Allowed_browser_emits_process_title_and_url()
    {
        var hwnd = _desktop.Add(new FakeWindow("chrome", "Docs", "https://docs.example.com/page"));

        var allowed = Processor(domains: ["bank.com"]).Process(Foreground(hwnd));

        Assert.True(allowed);
        var evt = Assert.IsType<WindowEvent>(Assert.Single(_sink.Events));
        Assert.Equal("foreground", evt.Type);
        Assert.Equal("chrome", evt.Process);
        Assert.Equal("Docs", evt.Title);
        Assert.Equal("https://docs.example.com/page", evt.Url);
    }

    [Fact]
    public void Non_browser_never_triggers_a_url_read()
    {
        var hwnd = _desktop.Add(new FakeWindow("notepad", "notes.txt - Notepad"));

        Processor(domains: ["bank.com"]).Process(Foreground(hwnd));

        Assert.Empty(_desktop.UrlReads);
        var evt = Assert.IsType<WindowEvent>(Assert.Single(_sink.Events));
        Assert.Null(evt.Url);
    }

    [Fact]
    public void Unknown_process_fails_closed()
    {
        var hwnd = _desktop.Add(new FakeWindow(null, "???"));

        Assert.False(Processor().Process(Foreground(hwnd)));
        Assert.Empty(_desktop.TitleReads);
    }

    [Fact]
    public void Browser_with_unreadable_url_is_excluded_when_domain_rules_exist()
    {
        var hwnd = _desktop.Add(new FakeWindow("chrome", "Something", Url: null));

        Assert.False(Processor(domains: ["bank.com"]).Process(Foreground(hwnd)));
        Assert.Empty(_desktop.TitleReads);
    }

    [Fact]
    public void Browser_with_unreadable_url_is_allowed_without_domain_rules()
    {
        var hwnd = _desktop.Add(new FakeWindow("chrome", "Something", Url: null));

        Assert.True(Processor().Process(Foreground(hwnd)));
        Assert.Null(Assert.IsType<WindowEvent>(Assert.Single(_sink.Events)).Url);
    }

    [Fact]
    public void Navigating_into_an_excluded_domain_via_title_change_is_caught()
    {
        var processor = Processor(domains: ["bank.com"]);
        var hwnd = _desktop.Add(new FakeWindow("chrome", "News", "https://news.example.com"));
        processor.Process(Foreground(hwnd));

        _desktop.Update(hwnd, new FakeWindow("chrome", "Bank login", "https://bank.com/login"));
        var allowed = processor.Process(TitleChange(hwnd));

        Assert.False(allowed);
        Assert.Collection(_sink.Events,
            e => Assert.Equal("News", Assert.IsType<WindowEvent>(e).Title),
            e => Assert.IsType<ExcludedContextEvent>(e));
    }

    [Fact]
    public void Repeated_excluded_signals_emit_one_marker_and_leaving_resumes_events()
    {
        var processor = Processor(apps: ["KeePass"]);
        var secret = _desktop.Add(new FakeWindow("KeePass", "vault"));
        var editor = _desktop.Add(new FakeWindow("Code", "main.cs"));

        processor.Process(Foreground(secret));
        processor.Process(TitleChange(secret));
        processor.Process(TitleChange(secret));
        processor.Process(Foreground(editor));

        Assert.Collection(_sink.Events,
            e => Assert.IsType<ExcludedContextEvent>(e),
            e => Assert.Equal("Code", Assert.IsType<WindowEvent>(e).Process));
    }

    [Fact]
    public void Returning_to_the_same_window_after_an_excluded_one_emits_again()
    {
        var processor = Processor(apps: ["KeePass"]);
        var editor = _desktop.Add(new FakeWindow("Code", "main.cs"));
        var secret = _desktop.Add(new FakeWindow("KeePass", "vault"));

        processor.Process(Foreground(editor));
        processor.Process(Foreground(secret));
        processor.Process(Foreground(editor));

        Assert.Equal(3, _sink.Events.Count);
        Assert.IsType<WindowEvent>(_sink.Events[2]);
    }

    [Fact]
    public void Unchanged_title_is_deduplicated_and_changed_title_is_emitted_as_title_change()
    {
        var processor = Processor();
        var hwnd = _desktop.Add(new FakeWindow("Code", "main.cs"));

        processor.Process(Foreground(hwnd));
        processor.Process(TitleChange(hwnd));
        _desktop.Update(hwnd, new FakeWindow("Code", "● main.cs"));
        processor.Process(TitleChange(hwnd));

        Assert.Collection(_sink.Events,
            e => Assert.Equal("foreground", e.Type),
            e => Assert.Equal("title_change", e.Type));
    }
}

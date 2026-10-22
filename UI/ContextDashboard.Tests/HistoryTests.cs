using System.IO.Compression;
using System.Text;
using System.Text.Json;
using ContextDashboard.Data;
using ContextDashboard.Services;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Tests;

/// <summary>The history slider: which screenshot and page text belong to a moment, the highlight, and the screenshot opt-in.</summary>
public sealed class HistoryTests : IDisposable
{
    private static readonly DateTimeOffset Noon = new(DateTime.Today.AddHours(12), TimeZoneInfo.Local.GetUtcOffset(DateTime.Today.AddHours(12)));
    private readonly TempDb _context = new();
    private readonly string _dir = Directory.CreateTempSubdirectory("hist-").FullName;
    private string MemoryPath => Path.Combine(_dir, "memory.db");
    private string ShotsPath => Path.Combine(_dir, "screenshots.db");
    private string RawPath => Path.Combine(_dir, "raw_buffer.db");

    public void Dispose()
    {
        _context.Dispose();
        try { Directory.Delete(_dir, recursive: true); } catch (IOException) { }
    }

    private HistoryDb Db() => new(_context.Path, MemoryPath, ShotsPath, RawPath);

    private static void Exec(string path, string sql, params (string, object)[] args)
    {
        using var c = new SqliteConnection($"Data Source={path};Pooling=False");
        c.Open();
        using var cmd = c.CreateCommand();
        cmd.CommandText = sql;
        foreach (var (n, v) in args) cmd.Parameters.AddWithValue(n, v);
        cmd.ExecuteNonQuery();
    }

    /// <summary>The same steps as the worker: zlib, then DPAPI with the text entropy.</summary>
    private static byte[] ProtectPage(string[] words, bool[] breaks)
    {
        var json = JsonSerializer.SerializeToUtf8Bytes(new { captures = 1, fragments = new[] { new { words, breaks } } });
        using var output = new MemoryStream();
        using (var zlib = new ZLibStream(output, CompressionLevel.Optimal, leaveOpen: true)) zlib.Write(json);
        return Dpapi.Protect(output.ToArray(), Dpapi.TextEntropy);
    }

    private void Setup()
    {
        // ids 1-3: one window; 4: another window; 5: back to the first title
        _context.AddRecord(Noon, "msedge", "save");
        _context.AddRecord(Noon.AddSeconds(10), "msedge", "discard");
        _context.AddRecord(Noon.AddSeconds(20), "msedge", "discard");
        _context.AddRecord(Noon.AddSeconds(30), "Code", "discard");
        _context.AddRecord(Noon.AddSeconds(40), "msedge", "discard");

        Exec(ShotsPath, "CREATE TABLE screenshots (id INTEGER PRIMARY KEY, ts INTEGER, context_record_id INTEGER, width INTEGER, "
                        + "height INTEGER, format TEXT, encoding TEXT, bytes INTEGER, image_blob BLOB)");
        var jpeg = Encoding.ASCII.GetBytes("not really a jpeg");
        Exec(ShotsPath, "INSERT INTO screenshots VALUES (1, $ts, 1, 4, 2, 'jpeg', 'dpapi/v1', 10, $b)",
            ("$ts", ContextDb.ToEpochUs(Noon)), ("$b", Dpapi.Protect(jpeg, Dpapi.ScreenshotEntropy)));

        var words = Enumerable.Range(0, 20).Select(i => $"w{i}").ToArray();
        var breaks = words.Select((_, i) => i == 10).ToArray();
        Exec(MemoryPath, "CREATE TABLE documents (doc_id TEXT PRIMARY KEY, title TEXT, fragments_blob BLOB)");
        Exec(MemoryPath, "INSERT INTO documents VALUES ('d1', 'Article', $b)", ("$b", ProtectPage(words, breaks)));
        Exec(MemoryPath, "CREATE TABLE moments (id INTEGER PRIMARY KEY, doc_id TEXT, ts INTEGER, context_record_id INTEGER, "
                         + "anchors TEXT, n_words INTEGER, fragment INTEGER, start_word INTEGER)");
        Exec(MemoryPath, "INSERT INTO moments VALUES (1, 'd1', $ts, 2, '[]', 5, 0, 8)", ("$ts", ContextDb.ToEpochUs(Noon.AddSeconds(10))));

        Exec(RawPath, "CREATE TABLE raw_captures (id INTEGER PRIMARY KEY, ts INTEGER, context_record_id INTEGER, text_blob BLOB)");
        Exec(RawPath, "INSERT INTO raw_captures VALUES (1, $ts, 5, x'00')", ("$ts", ContextDb.ToEpochUs(Noon.AddSeconds(40))));
    }

    [Fact]
    public void A_moment_shows_its_own_screenshot_or_an_earlier_one_of_the_same_window_only()
    {
        Setup();
        var day = Db().LoadDay(DateOnly.FromDateTime(DateTime.Today));

        Assert.Equal([1L, 2, 3, 4, 5], day.Points.Select(p => p.Id));
        Assert.Equal(1, day.ShotFor(0)?.Id);
        Assert.Equal(1, day.ShotFor(2)?.Id);   // same window, saved earlier
        Assert.Null(day.ShotFor(3));           // another app
        Assert.Null(day.ShotFor(4));           // back to Edge, but another window came in between
        Assert.Null(day.MomentFor(0));         // its text came later in the window
        Assert.Equal(8, day.MomentFor(2)?.StartWord);
        Assert.True(day.TextWaiting(4));
        Assert.False(day.TextWaiting(2));
    }

    [Fact]
    public void The_slider_position_picks_the_last_moment_at_or_before_it()
    {
        Setup();
        var day = Db().LoadDay(DateOnly.FromDateTime(DateTime.Today));
        Assert.Equal(0, day.IndexAt(ContextDb.ToEpochUs(Noon.AddSeconds(-5))));
        Assert.Equal(1, day.IndexAt(ContextDb.ToEpochUs(Noon.AddSeconds(15))));
        Assert.Equal(4, day.IndexAt(ContextDb.ToEpochUs(Noon.AddHours(3))));
    }

    [Fact]
    public void Screenshots_and_pages_are_decrypted_for_display()
    {
        Setup();
        var db = Db();
        Assert.Equal("not really a jpeg", Encoding.ASCII.GetString(db.ShotJpeg(1)!));
        var page = db.Page("d1")!;
        Assert.Equal("Article", page.Title);
        Assert.Equal(20, page.Fragments[0].Words.Length);
    }

    [Fact]
    public void The_excerpt_highlights_what_was_on_screen_and_keeps_line_breaks()
    {
        Setup();
        var db = Db();
        var page = db.Page("d1")!;
        var moment = new HistoryMoment("d1", 0, 2, Words: 5, Fragment: 0, StartWord: 8);

        var excerpt = HistoryDb.Excerpt(page, moment, context: 3);
        Assert.Equal("w5 w6 w7", excerpt.Before);
        Assert.Equal(" w8 w9\nw10 w11 w12", excerpt.Highlight);
        Assert.Equal(" w13 w14 w15", excerpt.After);
        Assert.True(excerpt.CutBefore);
        Assert.True(excerpt.CutAfter);

        var unplaced = HistoryDb.Excerpt(page, moment with { Fragment = null, StartWord = null });
        Assert.Equal("", unplaced.Highlight);
        Assert.StartsWith("w0 w1", unplaced.Before);
    }

    [Fact]
    public void Deleting_screenshots_empties_the_store()
    {
        Setup();
        var db = Db();
        Assert.Equal(1, db.Screenshots().Shots);
        Assert.Equal(1, db.DeleteAllScreenshots());
        Assert.Equal(0, db.Screenshots().Shots);
        Assert.Null(db.LoadDay(DateOnly.FromDateTime(DateTime.Today)).ShotFor(0));
    }

    [Fact]
    public void Missing_files_mean_an_empty_history_not_an_error()
    {
        var db = new HistoryDb(Path.Combine(_dir, "none.db"), MemoryPath, ShotsPath, RawPath);
        Assert.Empty(db.Days());
        Assert.Empty(db.LoadDay(DateOnly.FromDateTime(DateTime.Today)).Points);
        Assert.Equal(ScreenshotStats.Empty, db.Screenshots());
    }
}

/// <summary>Screenshots are their own opt-in: off by default, on only with consent, and only passed to a worker that knows the flag.</summary>
public class ScreenshotSettingsTests
{
    private static AppSettings Installed() =>
        new() { WorkerExe = $@"C:\app{Guid.NewGuid():N}\worker\worker.exe", CollectorExe = @"C:\app\collector\CsharpCollector.exe", DbPath = @"C:\d\context.db", PipeName = "p" };

    private static ServiceControl Services(AppSettings s, string help = "usage: --screenshots --shots-db") => new(s, (_, _) => help);

    [Fact]
    public void They_are_off_by_default()
    {
        var s = Installed();
        Assert.False(s.ScreenshotsActive);
        Assert.DoesNotContain("--screenshots", Services(s).WorkerCommand());
        Assert.Equal(@"C:\d\screenshots.db", s.ScreenshotsPath);
    }

    [Fact]
    public void Opting_in_passes_the_flag_and_the_limits()
    {
        var s = Installed();
        s.OptInScreenshots(DateTimeOffset.Parse("2026-10-24T10:00:00Z"));
        Assert.True(s.ScreenshotsActive);
        Assert.Contains("--screenshots --shots-ttl-days 30 --shots-max-mb 5000", Services(s).WorkerCommand());
        Assert.False(s.ScreenTextActive); // a separate choice from screen text
        Assert.DoesNotContain("--shots-quality", Services(s).WorkerCommand()); // an older worker without the flag
    }

    [Fact]
    public void The_chosen_limits_and_quality_reach_the_worker()
    {
        var s = Installed();
        s.OptInScreenshots(DateTimeOffset.Parse("2026-10-24T10:00:00Z"));
        Assert.Contains("--shots-quality 50", Services(s, "usage: --screenshots --shots-quality").WorkerCommand());
        (s.ScreenshotsKeepDays, s.ScreenshotsMaxMb, s.ScreenshotsQuality) = (90, 20_000, 500);
        var command = Services(s, "usage: --screenshots --shots-quality").WorkerCommand();
        Assert.Contains("--shots-ttl-days 90 --shots-max-mb 20000", command);
        Assert.Contains("--shots-quality 95", command); // clamped
    }

    [Fact]
    public void An_older_worker_is_not_sent_a_flag_it_would_exit_on()
    {
        var s = Installed();
        s.OptInScreenshots(DateTimeOffset.Now);
        Assert.DoesNotContain("--screenshots", Services(s, help: "usage: --no-screen-text").WorkerCommand());
    }

    [Fact]
    public void The_switch_alone_is_not_consent_and_opting_out_withdraws_it()
    {
        var s = Installed();
        s.ScreenshotsEnabled = true;
        Assert.False(s.ScreenshotsActive);
        s.OptInScreenshots(DateTimeOffset.Now);
        s.OptOutScreenshots();
        Assert.False(s.ScreenshotsActive);
        Assert.Equal(0, s.ScreenshotsConsentVersion);
    }
}

/// <summary>Titles shown on the Memories page match the ones the model is given (python-worker/features/titles.py).</summary>
public class TitleTests
{
    [Theory]
    [InlineData("(3) Inbox and 2 more pages - Personal - Microsoft\u200b Edge", "Inbox")]
    [InlineData("all features in zen browser - YouTube — Zen Browser", "all features in zen browser - YouTube")]
    [InlineData("CLAUDE.md - J26-IT-308 - Research - Visual Studio Code", "CLAUDE.md - J26-IT-308 - Research")]
    [InlineData("● main.py - Visual Studio Code", "main.py")]
    [InlineData("Microsoft Edge", "Microsoft Edge")]
    [InlineData("", "")]
    public void Display_titles_drop_the_browser_profile_and_counters(string raw, string expected) =>
        Assert.Equal(expected, ContextDashboard.Data.Titles.Display(raw));
}

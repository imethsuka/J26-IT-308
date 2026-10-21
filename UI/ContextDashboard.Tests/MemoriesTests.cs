using System.Text.Json;
using ContextDashboard.Data;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Tests;

/// <summary>The Memories page: one sentence per memory, from the model's summary when there is one, else from the stored facts.</summary>
public class MemoriesTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), $"mem-{Guid.NewGuid():N}");

    public MemoriesTests() => Directory.CreateDirectory(_dir);

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        try { Directory.Delete(_dir, true); } catch (IOException) { }
    }

    private static MemoryRow Row(string title = "Q3 plan", string app = "msedge", string site = "docs.example.com", int visit = 1,
                                 double seconds = 600, string summary = "", string model = "") =>
        new(1, DateTimeOffset.Now, app, site, title, "high", "0.80", visit, "", true, summary, seconds, 120, model);

    [Fact]
    public void Without_a_model_summary_the_sentence_is_built_only_from_stored_facts()
    {
        var r = Row();

        Assert.False(r.IsModelSummary);
        Assert.Equal("Spent 10 min on “Q3 plan” in msedge (docs.example.com).", r.Headline);
    }

    [Fact]
    public void Short_visits_repeat_visits_and_missing_pieces_read_naturally()
    {
        Assert.Contains("under a minute", Row(seconds: 20).Headline);
        Assert.EndsWith(", visit 3.", Row(visit: 3).Headline);
        Assert.Equal("Spent 10 min on a page in code.", Row(title: "", app: "code", site: "-").Headline);
    }

    [Fact]
    public void A_model_summary_wins_over_the_template()
    {
        var r = Row(summary: "  Reviewed the Q3 plan and edited two sections.  ", model: "qwen2.5-1.5b");

        Assert.True(r.IsModelSummary);
        Assert.Equal("Reviewed the Q3 plan and edited two sections.", r.Headline);
    }

    [Fact]
    public void Day_labels_and_meta_line()
    {
        Assert.Equal("Today", Row().DayLabel);
        Assert.Equal("Yesterday", (Row() with { Start = DateTimeOffset.Now.AddDays(-1) }).DayLabel);
        Assert.Equal("msedge  ·  docs.example.com  ·  visit 2  ·  importance 0.80  ·  120 words kept", Row(visit: 2).Meta);
    }

    [Fact]
    public void One_word_is_not_plural()
    {
        Assert.EndsWith("1 word kept", (Row() with { Words = 1 }).Meta);
    }

    [Fact]
    public void Time_range_shows_the_end_only_when_it_is_later()
    {
        var start = new DateTimeOffset(2026, 10, 21, 9, 0, 0, TimeZoneInfo.Local.GetUtcOffset(new DateTime(2026, 10, 21, 9, 0, 0)));
        Assert.Equal("09:00", (Row() with { Start = start }).TimeRange);
        Assert.Equal("09:00 to 09:25", (Row() with { Start = start, End = start.AddMinutes(25) }).TimeRange);
    }

    [Fact]
    public void Entries_carry_the_model_name_duration_and_word_count_from_the_database()
    {
        var path = Path.Combine(_dir, "memory.db");
        var json = JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["app"] = "chrome", ["domain"] = "example.com", ["title"] = "Notes", ["start"] = "2026-10-20T09:00:00+00:00",
            ["end"] = "2026-10-20T09:30:00+00:00", ["active_seconds"] = 900.5, ["words_added"] = 250, ["visit_number"] = 2,
        });
        using (var c = new SqliteConnection($"Data Source={path};Pooling=False"))
        {
            c.Open();
            using var cmd = c.CreateCommand();
            cmd.CommandText = @"
                CREATE TABLE episodes (id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, start_ts INTEGER);
                CREATE TABLE memory_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, episode_id INTEGER, tier TEXT, importance REAL,
                    entry_json TEXT, summary TEXT, summary_model TEXT, text_persisted INTEGER);
                INSERT INTO episodes(doc_id, start_ts) VALUES ('d', 1);
                INSERT INTO memory_entries(episode_id, tier, importance, entry_json, summary, summary_model, text_persisted)
                    VALUES (1, 'high', 0.7, $j, 'Wrote notes.', 'qwen2.5-1.5b', 1);";
            cmd.Parameters.AddWithValue("$j", json);
            cmd.ExecuteNonQuery();
        }

        var row = new ScreenTextDb(Path.Combine(_dir, "raw.db"), path).Entries(10).Single();

        Assert.Equal("qwen2.5-1.5b", row.SummaryModel);
        Assert.Equal(900.5, row.ActiveSeconds);
        Assert.Equal(250, row.Words);
        Assert.Equal("Wrote notes.", row.Headline);
        Assert.NotNull(row.End);
    }

    [Fact]
    public void An_older_memory_database_without_the_model_column_still_reads()
    {
        var path = Path.Combine(_dir, "old.db");
        using (var c = new SqliteConnection($"Data Source={path};Pooling=False"))
        {
            c.Open();
            using var cmd = c.CreateCommand();
            cmd.CommandText = @"
                CREATE TABLE episodes (id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, start_ts INTEGER);
                CREATE TABLE memory_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, episode_id INTEGER, tier TEXT, importance REAL,
                    entry_json TEXT, summary TEXT, text_persisted INTEGER);
                INSERT INTO episodes(doc_id, start_ts) VALUES ('d', 1);
                INSERT INTO memory_entries(episode_id, tier, importance, entry_json, summary, text_persisted)
                    VALUES (1, 'low', NULL, '{""app"":""code"",""title"":""x"",""start"":""2026-10-20T09:00:00+00:00""}', NULL, 0);";
            cmd.ExecuteNonQuery();
        }

        var row = new ScreenTextDb(Path.Combine(_dir, "raw.db"), path).Entries(10).Single();

        Assert.Equal("", row.SummaryModel);
        Assert.False(row.IsModelSummary);
    }
}

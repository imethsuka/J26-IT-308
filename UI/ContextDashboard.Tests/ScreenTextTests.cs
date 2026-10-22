using System.Text.Json;
using ContextDashboard.Data;
using ContextDashboard.Services;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Tests;

/// <summary>Screen text is opt-in: off by default, on only with consent, and the choice reaches every process.</summary>
public class ScreenTextSettingsTests
{
    private static AppSettings Installed() =>
        new() { WorkerExe = @"C:\app\worker\worker.exe", CollectorExe = @"C:\app\collector\CsharpCollector.exe", DbPath = @"C:\d\context.db", PipeName = "p" };

    [Fact]
    public void It_is_off_by_default_and_every_process_is_told_so()
    {
        var s = Installed();
        Assert.False(s.ScreenTextActive);
        Assert.EndsWith("--screen-text off", new ServiceControl(s).CollectorCommand());
        Assert.EndsWith("--no-screen-text", new ServiceControl(s).WorkerCommand());
        var learn = new LearningRunner(s);
        Assert.DoesNotContain("--screen-memory", learn.Command(false, "v3").Args);
        Assert.DoesNotContain("--screen-memory", learn.Command(false, "v3", daemon: true).Args);
    }

    [Fact]
    public void Settings_from_an_older_install_without_the_fields_stay_off()
    {
        var s = JsonSerializer.Deserialize<AppSettings>("{\"DbPath\":\"x.db\",\"BackgroundLearning\":true}")!;
        Assert.False(s.ScreenTextEnabled);
        Assert.False(s.ScreenTextActive);
    }

    [Fact]
    public void Opting_in_turns_it_on_everywhere_and_records_the_consent()
    {
        var s = Installed();
        s.OptInScreenText(DateTimeOffset.Parse("2026-10-20T10:00:00Z"));

        Assert.True(s.ScreenTextActive);
        Assert.Equal(AppSettings.CurrentConsentVersion, s.ScreenTextConsentVersion);
        Assert.StartsWith("2026-10-20T10:00:00", s.ScreenTextConsentAt);
        Assert.EndsWith("--screen-text on", new ServiceControl(s).CollectorCommand());
        Assert.DoesNotContain("--no-screen-text", new ServiceControl(s).WorkerCommand());
        Assert.Contains("--screen-memory", new LearningRunner(s).Command(false, "v3").Args);
        Assert.Contains("--screen-memory", new LearningRunner(s).Command(false, "v3", daemon: true).Args);
    }

    [Fact]
    public void The_switch_alone_is_not_consent()
    {
        var s = Installed();
        s.ScreenTextEnabled = true; // e.g. a hand-edited dashboard.json
        Assert.False(s.ScreenTextActive);
        Assert.EndsWith("--screen-text off", new ServiceControl(s).CollectorCommand());
    }

    [Fact]
    public void A_changed_consent_text_asks_again()
    {
        var s = Installed();
        s.OptInScreenText(DateTimeOffset.Now);
        s.ScreenTextConsentVersion = AppSettings.CurrentConsentVersion - 1;
        Assert.False(s.ScreenTextActive);
    }

    [Fact]
    public void Developer_mode_adds_the_pass_through_flag_only_when_asked_and_only_to_the_job()
    {
        var s = Installed();
        s.OptInScreenText(DateTimeOffset.Now);
        Assert.DoesNotContain("--dev-passthrough-filter", new LearningRunner(s).Command(false, "v3").Args);

        s.ScreenTextKeepUnredacted = true;
        Assert.Contains("--dev-passthrough-filter", new LearningRunner(s).Command(false, "v3").Args);
        Assert.DoesNotContain("passthrough", new ServiceControl(s).WorkerCommand());
    }

    [Fact]
    public void Opting_out_withdraws_the_consent_and_the_developer_option()
    {
        var s = Installed();
        s.OptInScreenText(DateTimeOffset.Now);
        s.ScreenTextKeepUnredacted = true;

        s.OptOutScreenText();

        Assert.False(s.ScreenTextActive);
        Assert.False(s.ScreenTextKeepUnredacted);
        Assert.Equal(0, s.ScreenTextConsentVersion);
        Assert.DoesNotContain("--screen-memory", new LearningRunner(s).Command(false, "v3").Args);
    }

    [Fact]
    public void The_two_files_sit_beside_the_context_database()
    {
        var s = new AppSettings { DbPath = @"C:\d\context.db" };
        Assert.Equal(@"C:\d\raw_buffer.db", s.RawBufferPath);
        Assert.Equal(@"C:\d\memory.db", s.MemoryPath);
    }

    [Fact]
    public void The_consent_text_says_what_matters()
    {
        var text = AppSettings.ConsentText;
        Assert.Contains("never reads passwords", text);
        Assert.Contains("encrypted", text);
        Assert.Contains("7 days", text);
        Assert.Contains("Settings", text);
        Assert.Contains("Nothing leaves this PC", text);
        Assert.Contains("consent form", text);
    }
}

/// <summary>Uses the same table layout the worker writes (storage/raw_buffer.py, storage/memory_store.py).</summary>
public class ScreenTextDbTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "st-" + Guid.NewGuid().ToString("N"));
    private string Raw => Path.Combine(_dir, "raw_buffer.db");
    private string Memory => Path.Combine(_dir, "memory.db");

    public ScreenTextDbTests() => Directory.CreateDirectory(_dir);

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        try { Directory.Delete(_dir, true); } catch (IOException) { }
    }

    private static void Run(string path, string sql)
    {
        using var c = new SqliteConnection($"Data Source={path};Pooling=False");
        c.Open();
        using var cmd = c.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }

    private void MakeRaw(int rows, int blobBytes = 1000)
    {
        Run(Raw, "CREATE TABLE raw_captures (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, text_blob BLOB)");
        for (var i = 0; i < rows; i++)
            Run(Raw, $"INSERT INTO raw_captures(ts, text_blob) VALUES ({1_760_000_000_000_000L + i * 1_000_000L}, zeroblob({blobBytes}))");
        Run(Raw, "INSERT INTO raw_captures(ts, text_blob) VALUES (1760000000000000, NULL)"); // text dropped by the size cap
    }

    private void MakeMemory()
    {
        Run(Memory, @"
            CREATE TABLE documents (doc_id TEXT PRIMARY KEY, word_count INTEGER);
            CREATE TABLE episodes (id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, start_ts INTEGER);
            CREATE TABLE memory_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, episode_id INTEGER, tier TEXT, importance REAL,
                                         entry_json TEXT, summary TEXT, text_persisted INTEGER);
            INSERT INTO documents VALUES ('d1', 120);
            INSERT INTO episodes(doc_id, start_ts) VALUES ('d1', 1760000000000000), ('d1', 1760100000000000);");
        var older = JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["app"] = "chrome", ["domain"] = "medium.com", ["title"] = "Understanding LightGBM", ["start"] = "2026-10-20T09:00:00+00:00",
            ["n_captures"] = 14, ["mouse_scrolls"] = 120, ["key_presses"] = 12, ["visit_number"] = 1,
        });
        var newer = older.Replace("\"visit_number\":1", "\"visit_number\":2").Replace("09:00:00", "10:00:00");
        Run(Memory, "INSERT INTO memory_entries(episode_id, tier, importance, entry_json, summary, text_persisted) VALUES " +
                    $"(1, 'high', 0.82, '{older}', NULL, 1), (2, 'low', NULL, '{newer}', 'Read about LightGBM', 0)");
    }

    [Fact]
    public void Nothing_exists_before_anything_was_captured()
    {
        var db = new ScreenTextDb(Raw, Memory);
        Assert.Equal(RawBufferStats.Empty, db.Raw());
        Assert.Equal(MemoryStats.Empty, db.Memory());
        Assert.Empty(db.Entries(10));
        Assert.Equal((0L, 0L), db.DeleteAll());
        Assert.False(File.Exists(Raw)); // reading or deleting must not create the files
    }

    [Fact]
    public void Buffer_stats_are_counts_and_sizes_only()
    {
        MakeRaw(3, blobBytes: 500);
        var stats = new ScreenTextDb(Raw, Memory).Raw();
        Assert.Equal(4, stats.Captures);
        Assert.Equal(3, stats.WithText);
        Assert.Equal(1500, stats.TextBytes);
        Assert.True(stats.FileBytes > 0);
        Assert.NotNull(stats.Oldest);
    }

    [Fact]
    public void Entries_are_read_newest_first_with_their_fields()
    {
        MakeMemory();
        var db = new ScreenTextDb(Raw, Memory);

        var stats = db.Memory();
        Assert.Equal((1L, 2L, 2L), (stats.Documents, stats.Episodes, stats.Entries));
        var rows = db.Entries(10);

        Assert.Equal(2, rows.Count);
        Assert.Equal(2, rows[0].Visit);  // the later episode first
        Assert.Equal("low", rows[0].Tier);
        Assert.Equal("–", rows[0].Importance);
        Assert.False(rows[0].TextKept);
        Assert.Equal("Read about LightGBM", rows[0].Summary);

        Assert.Equal("high", rows[1].Tier);
        Assert.Equal("0.82", rows[1].Importance);
        Assert.Equal("medium.com", rows[1].Site);
        Assert.Equal("Understanding LightGBM", rows[1].Title);
        Assert.Equal("14 captures, 120 scrolls, 12 key presses", rows[1].Activity);
        Assert.True(rows[1].TextKept);
        Assert.Single(db.Entries(1));
    }

    [Fact]
    public void Delete_all_empties_both_files_and_keeps_them_usable()
    {
        MakeRaw(5);
        MakeMemory();
        var db = new ScreenTextDb(Raw, Memory);
        var sizeBefore = new FileInfo(Raw).Length;

        var (captures, entries) = db.DeleteAll();

        Assert.Equal((6L, 2L), (captures, entries));
        Assert.Equal(0, db.Raw().Captures);
        Assert.Equal(0, db.Memory().Entries);
        Assert.Empty(db.Entries(10));
        Assert.True(new FileInfo(Raw).Length <= sizeBefore); // compacted: deleted text does not linger in free pages
    }

    [Fact]
    public void Reading_does_not_block_the_writer()
    {
        MakeRaw(1);
        var db = new ScreenTextDb(Raw, Memory);
        db.Raw();
        Run(Raw, "INSERT INTO raw_captures(ts, text_blob) VALUES (1, zeroblob(10))");
        Assert.Equal(3, db.Raw().Captures);
    }
}

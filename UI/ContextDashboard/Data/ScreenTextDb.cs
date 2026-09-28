using System.Globalization;
using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Data;

/// <summary>What is buffered right now. Counts and sizes only: the dashboard never decrypts or shows the raw text.</summary>
public sealed record RawBufferStats(long Captures, long WithText, long TextBytes, long FileBytes, DateTimeOffset? Oldest)
{
    public static readonly RawBufferStats Empty = new(0, 0, 0, 0, null);
}

public sealed record MemoryStats(long Documents, long Episodes, long Entries, long FileBytes)
{
    public static readonly MemoryStats Empty = new(0, 0, 0, 0);
}

/// <summary>One memory entry, as the nightly job wrote it (metadata and counts; it holds no screen text).</summary>
public sealed record MemoryRow(
    long Id, DateTimeOffset Start, string App, string Site, string Title, string Tier, string Importance, int Visit,
    string Activity, bool TextKept, string Summary,
    double ActiveSeconds = 0, int Words = 0, string SummaryModel = "", DateTimeOffset? End = null)
{
    /// <summary>The importance as a number (0 when the entry has none), for ranking.</summary>
    public double ImportanceValue => double.TryParse(Importance, NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : 0;

    /// <summary>True when a language model wrote the sentence; false when it is built from the entry's own numbers.</summary>
    public bool IsModelSummary => !string.IsNullOrWhiteSpace(Summary);

    /// <summary>
    /// What the memory says, in one sentence. The model's summary when there is one, otherwise a sentence assembled
    /// from the stored facts only (app, site, page title, time spent, visit number), so it can never say more than was recorded.
    /// </summary>
    public string Headline
    {
        get
        {
            if (IsModelSummary) return Summary.Trim();
            var what = string.IsNullOrWhiteSpace(Title) ? "a page" : $"\u201C{Title.Trim()}\u201D";
            var where = string.IsNullOrWhiteSpace(Site) || Site == "-" ? App : $"{App} ({Site})";
            var spent = ActiveSeconds < 60 ? "under a minute" : $"{Math.Round(ActiveSeconds / 60):0} min";
            var visit = Visit > 1 ? $", visit {Visit}" : "";
            return $"Spent {spent} on {what} in {where}{visit}.";
        }
    }

    public string TimeRange => End is { } end && end.ToLocalTime() > Start
        ? $"{Start:HH:mm} to {end.ToLocalTime():HH:mm}" : $"{Start:HH:mm}";

    public string DayLabel
    {
        get
        {
            var day = Start.Date;
            var today = DateTime.Now.Date;
            return day == today ? "Today" : day == today.AddDays(-1) ? "Yesterday" : Start.ToString("dddd d MMMM");
        }
    }

    public string Meta
    {
        get
        {
            var parts = new List<string> { App };
            if (!string.IsNullOrWhiteSpace(Site) && Site != "-") parts.Add(Site);
            if (Visit > 0) parts.Add($"visit {Visit}");
            if (Importance != "\u2013") parts.Add($"importance {Importance}");
            if (TextKept && Words > 0) parts.Add($"{Words:N0} word{(Words == 1 ? "" : "s")} kept");
            return string.Join("  \u00B7  ", parts);
        }
    }
}

/// <summary>
/// Read access to <c>raw_buffer.db</c> (the raw, encrypted screen text) and <c>memory.db</c> (the entries built from it),
/// plus the one write the user is entitled to: delete everything. Like <see cref="ContextDb"/>, nothing stays open between calls.
/// </summary>
public sealed class ScreenTextDb
{
    private readonly string _rawPath, _memoryPath;

    public ScreenTextDb(string rawPath, string memoryPath)
    {
        _rawPath = rawPath;
        _memoryPath = memoryPath;
    }

    private static SqliteConnection Open(string path, bool readOnly)
    {
        var connection = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = path,
            Mode = readOnly ? SqliteOpenMode.ReadOnly : SqliteOpenMode.ReadWrite,
            Pooling = false,
            DefaultTimeout = 10, // the worker or the job may be writing: wait rather than fail
        }.ToString());
        connection.Open();
        return connection;
    }

    private static bool HasColumn(SqliteConnection db, string table, string column)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = $"SELECT 1 FROM pragma_table_info('{table}') WHERE name = $c";
        cmd.Parameters.AddWithValue("$c", column);
        return cmd.ExecuteScalar() is not null;
    }

    private static long Scalar(SqliteConnection db, string sql)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = sql;
        var value = cmd.ExecuteScalar();
        return value is null or DBNull ? 0 : Convert.ToInt64(value, CultureInfo.InvariantCulture);
    }

    /// <summary>Size of the database file plus its WAL, which holds recent writes.</summary>
    private static long SizeOnDisk(string path) =>
        new[] { path, path + "-wal" }.Where(File.Exists).Sum(p => new FileInfo(p).Length);

    public RawBufferStats Raw()
    {
        if (!File.Exists(_rawPath)) return RawBufferStats.Empty;
        using var db = Open(_rawPath, readOnly: true);
        var oldest = Scalar(db, "SELECT COALESCE(MIN(ts), 0) FROM raw_captures");
        return new RawBufferStats(
            Scalar(db, "SELECT COUNT(*) FROM raw_captures"),
            Scalar(db, "SELECT COUNT(text_blob) FROM raw_captures"),
            Scalar(db, "SELECT COALESCE(SUM(LENGTH(text_blob)), 0) FROM raw_captures"),
            SizeOnDisk(_rawPath),
            oldest == 0 ? null : DateTimeOffset.FromUnixTimeMilliseconds(oldest / 1000));
    }

    public MemoryStats Memory()
    {
        if (!File.Exists(_memoryPath)) return MemoryStats.Empty;
        using var db = Open(_memoryPath, readOnly: true);
        return new MemoryStats(
            Scalar(db, "SELECT COUNT(*) FROM documents"),
            Scalar(db, "SELECT COUNT(*) FROM episodes"),
            Scalar(db, "SELECT COUNT(*) FROM memory_entries"),
            SizeOnDisk(_memoryPath));
    }

    private static bool HasTable(SqliteConnection db, string table)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = $t";
        cmd.Parameters.AddWithValue("$t", table);
        return cmd.ExecuteScalar() is not null;
    }

    /// <summary>
    /// How many memories are waiting for a model sentence: they kept page text, and none of their page's text came from the
    /// unredacted developer filter (those are never summarised). Zero when the file or its columns are missing.
    /// </summary>
    public long Waiting()
    {
        if (!File.Exists(_memoryPath)) return 0;
        try
        {
            using var db = Open(_memoryPath, readOnly: true);
            return Scalar(db,
                "SELECT COUNT(*) FROM memory_entries e WHERE e.needs_summary = 1 AND e.text_persisted = 1 AND NOT EXISTS " +
                "(SELECT 1 FROM memory_entries x WHERE x.doc_id = e.doc_id AND COALESCE(x.filter_version, '') LIKE 'passthrough%')");
        }
        catch (SqliteException)
        {
            return 0;
        }
    }

    /// <summary>Notes the learning job wrote for sessions, by session key. Empty when it has not run (the table is missing).</summary>
    public IReadOnlyDictionary<string, SessionNote> SessionNotes()
    {
        var notes = new Dictionary<string, SessionNote>();
        if (!File.Exists(_memoryPath)) return notes;
        using var db = Open(_memoryPath, readOnly: true);
        if (!HasTable(db, "session_notes")) return notes;
        using var cmd = db.CreateCommand();
        cmd.CommandText = "SELECT key, title, tag, tldr, model FROM session_notes";
        using var reader = cmd.ExecuteReader();
        string Text(int i) => reader.IsDBNull(i) ? "" : reader.GetString(i);
        while (reader.Read()) notes[reader.GetString(0)] = new SessionNote(Text(1), Text(2), Text(3), Text(4));
        return notes;
    }

    /// <summary>Day recaps by day (yyyy-MM-dd, local).</summary>
    public IReadOnlyDictionary<string, DayNote> DayNotes()
    {
        var notes = new Dictionary<string, DayNote>();
        if (!File.Exists(_memoryPath)) return notes;
        using var db = Open(_memoryPath, readOnly: true);
        if (!HasTable(db, "day_notes")) return notes;
        using var cmd = db.CreateCommand();
        cmd.CommandText = "SELECT day, sessions_key, tldr, model FROM day_notes";
        using var reader = cmd.ExecuteReader();
        string Text(int i) => reader.IsDBNull(i) ? "" : reader.GetString(i);
        while (reader.Read()) notes[reader.GetString(0)] = new DayNote(Text(1), Text(2), Text(3));
        return notes;
    }

    /// <summary>The newest entries first.</summary>
    public IReadOnlyList<MemoryRow> Entries(int limit)
    {
        if (!File.Exists(_memoryPath)) return [];
        using var db = Open(_memoryPath, readOnly: true);
        using var cmd = db.CreateCommand();
        cmd.CommandText =
            "SELECT e.id, e.tier, e.importance, e.entry_json, e.summary, e.text_persisted, " +
            (HasColumn(db, "memory_entries", "summary_model") ? "e.summary_model " : "NULL ") +
            "FROM memory_entries e JOIN episodes p ON p.id = e.episode_id ORDER BY p.start_ts DESC, e.id DESC LIMIT $n";
        cmd.Parameters.AddWithValue("$n", limit);
        var rows = new List<MemoryRow>();
        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            using var json = JsonDocument.Parse(reader.GetString(3));
            var root = json.RootElement;
            string Str(string name) => root.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";
            int Int(string name) => root.TryGetProperty(name, out var v) && v.TryGetInt32(out var n) ? n : 0;
            var start = DateTimeOffset.TryParse(Str("start"), CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var t)
                ? t.ToLocalTime() : DateTimeOffset.MinValue;
            DateTimeOffset? end = DateTimeOffset.TryParse(Str("end"), CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var t2)
                ? t2.ToLocalTime() : null;
            var seconds = root.TryGetProperty("active_seconds", out var sec) && sec.TryGetDouble(out var sv) ? sv : 0;
            rows.Add(new MemoryRow(
                reader.GetInt64(0), start, Str("app"), Str("domain"), Str("title"), reader.GetString(1),
                reader.IsDBNull(2) ? "–" : reader.GetDouble(2).ToString("0.00", CultureInfo.InvariantCulture),
                Int("visit_number"),
                $"{Int("n_captures")} captures, {Int("mouse_scrolls")} scrolls, {Int("key_presses")} key presses",
                reader.GetInt64(5) == 1, reader.IsDBNull(4) ? "" : reader.GetString(4),
                seconds, Int("words_added"), reader.IsDBNull(6) ? "" : reader.GetString(6), end));
        }
        return rows;
    }

    /// <summary>
    /// Deletes every captured text and every memory entry, then compacts the files so nothing lingers in free pages.
    /// Returns how many raw captures and entries were removed. This is the only place the dashboard writes to these files.
    /// </summary>
    public (long Captures, long Entries) DeleteAll()
    {
        long captures = 0, entries = 0;
        if (File.Exists(_rawPath))
        {
            using var db = Open(_rawPath, readOnly: false);
            captures = Scalar(db, "SELECT COUNT(*) FROM raw_captures");
            Exec(db, "DELETE FROM raw_captures");
            Compact(db);
        }
        if (File.Exists(_memoryPath))
        {
            using var db = Open(_memoryPath, readOnly: false);
            entries = Scalar(db, "SELECT COUNT(*) FROM memory_entries");
            Exec(db, "DELETE FROM memory_entries; DELETE FROM episodes; DELETE FROM documents");
            Compact(db);
        }
        return (captures, entries);
    }

    private static void Exec(SqliteConnection db, string sql)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }

    private static void Compact(SqliteConnection db)
    {
        Exec(db, "VACUUM");
        Exec(db, "PRAGMA wal_checkpoint(TRUNCATE)");
    }
}

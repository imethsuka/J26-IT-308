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
    string Activity, bool TextKept, string Summary);

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

    /// <summary>The newest entries first.</summary>
    public IReadOnlyList<MemoryRow> Entries(int limit)
    {
        if (!File.Exists(_memoryPath)) return [];
        using var db = Open(_memoryPath, readOnly: true);
        using var cmd = db.CreateCommand();
        cmd.CommandText =
            "SELECT e.id, e.tier, e.importance, e.entry_json, e.summary, e.text_persisted " +
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
            rows.Add(new MemoryRow(
                reader.GetInt64(0), start, Str("app"), Str("domain"), Str("title"), reader.GetString(1),
                reader.IsDBNull(2) ? "–" : reader.GetDouble(2).ToString("0.00", CultureInfo.InvariantCulture),
                Int("visit_number"),
                $"{Int("n_captures")} captures, {Int("mouse_scrolls")} scrolls, {Int("key_presses")} key presses",
                reader.GetInt64(5) == 1, reader.IsDBNull(4) ? "" : reader.GetString(4)));
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

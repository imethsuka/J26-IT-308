using System.Globalization;
using System.Text;
using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Data;

/// <summary>One moment on the history slider: a stored context record (every snapshot the worker kept, forever).</summary>
public sealed record HistoryPoint(long Id, long TsUs, string App, string Title, string Site, bool Saved)
{
    public DateTimeOffset Time => ContextDb.FromEpochUs(TsUs).ToLocalTime();
}

/// <summary>A screenshot in <c>screenshots.db</c>, kept for a 'save' decision.</summary>
public sealed record HistoryShot(long Id, long TsUs, long RecordId);

/// <summary>Where one capture sits in its stored page (<c>memory.db</c> moments). No position when it can't be found.</summary>
public sealed record HistoryMoment(string DocId, long TsUs, long RecordId, int Words, int? Fragment, int? StartWord);

/// <summary>A stored page: its title and merged, privacy-filtered text as fragments of words.</summary>
public sealed record StoredPage(string DocId, string Title, IReadOnlyList<PageFragment> Fragments);

public sealed record PageFragment(string[] Words, bool[] Breaks);

/// <summary>Page text around what was on screen: the highlighted part, with some text before and after it.</summary>
public sealed record PageExcerpt(string Before, string Highlight, string After, bool CutBefore, bool CutAfter);

public sealed record ScreenshotStats(long Shots, long Bytes)
{
    public static readonly ScreenshotStats Empty = new(0, 0);
}

/// <summary>
/// One day of history, ready for the slider. Screenshots and text belong to the record they were taken with; a record
/// without its own is shown with the nearest earlier one <em>of the same window</em> (same app and title, nothing else in
/// between), never with another window's.
/// </summary>
public sealed class HistoryDay(
    DateOnly day, IReadOnlyList<HistoryPoint> points, IReadOnlyDictionary<long, HistoryShot> shots,
    IReadOnlyDictionary<long, HistoryMoment> moments, IReadOnlySet<long> buffered)
{
    private const int MaxLookBack = 2000;

    public DateOnly Day { get; } = day;
    public IReadOnlyList<HistoryPoint> Points { get; } = points;
    public int ShotCount => shots.Count;
    public int MomentCount => moments.Count;

    /// <summary>The last record at or before <paramref name="tsUs"/> (the first one when it is earlier than all).</summary>
    public int IndexAt(long tsUs)
    {
        int lo = 0, hi = Points.Count - 1, found = 0;
        while (lo <= hi)
        {
            var mid = (lo + hi) / 2;
            if (Points[mid].TsUs <= tsUs) { found = mid; lo = mid + 1; }
            else hi = mid - 1;
        }
        return found;
    }

    public HistoryShot? ShotFor(int index) => SameWindow(index, shots);

    public HistoryMoment? MomentFor(int index) => SameWindow(index, moments);

    /// <summary>True when this record's text is still in the 3-day raw buffer, waiting for the nightly job.</summary>
    public bool TextWaiting(int index) => index >= 0 && index < Points.Count && buffered.Contains(Points[index].Id);

    private T? SameWindow<T>(int index, IReadOnlyDictionary<long, T> byRecord) where T : class
    {
        if (index < 0 || index >= Points.Count) return null;
        var here = Points[index];
        for (var i = index; i >= 0 && index - i < MaxLookBack; i--)
        {
            var p = Points[i];
            if (!string.Equals(p.App, here.App, StringComparison.OrdinalIgnoreCase)
                || !string.Equals(p.Title, here.Title, StringComparison.Ordinal)) break;
            if (byRecord.TryGetValue(p.Id, out var value)) return value;
        }
        return null;
    }
}

/// <summary>
/// Read access for the history page: the timeline from <c>context.db</c>, screenshots from <c>screenshots.db</c>, page text
/// and capture positions from <c>memory.db</c>, and which records still wait in <c>raw_buffer.db</c> (metadata only: the
/// raw text is never opened here). Images and pages are decrypted in memory for display and never written anywhere.
/// The one write: deleting all screenshots.
/// </summary>
public sealed class HistoryDb(string contextPath, string memoryPath, string screenshotsPath, string rawPath)
{
    private const int ExcerptWords = 1500;

    public string ScreenshotsPath => screenshotsPath;

    private static SqliteConnection? Open(string path, bool readOnly = true)
    {
        if (!File.Exists(path)) return null;
        var connection = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = path,
            Mode = readOnly ? SqliteOpenMode.ReadOnly : SqliteOpenMode.ReadWrite,
            Pooling = false,
            DefaultTimeout = 10,
        }.ToString());
        connection.Open();
        return connection;
    }

    private static bool HasTable(SqliteConnection db, string table)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = $t";
        cmd.Parameters.AddWithValue("$t", table);
        return cmd.ExecuteScalar() is not null;
    }

    private static IEnumerable<T> Query<T>(SqliteConnection db, string sql, Func<SqliteDataReader, T> map, params (string, object)[] args)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = sql;
        foreach (var (name, value) in args) cmd.Parameters.AddWithValue(name, value);
        using var reader = cmd.ExecuteReader();
        while (reader.Read()) yield return map(reader);
    }

    private static (long From, long To) Range(DateOnly day)
    {
        var start = new DateTimeOffset(day.ToDateTime(TimeOnly.MinValue, DateTimeKind.Local));
        return (ContextDb.ToEpochUs(start), ContextDb.ToEpochUs(start.AddDays(1)));
    }

    /// <summary>Every local day that has records, oldest first.</summary>
    public IReadOnlyList<DateOnly> Days()
    {
        using var db = Open(contextPath);
        if (db is null) return [];
        return Query(db, "SELECT DISTINCT date(ts / 1000000, 'unixepoch', 'localtime') AS d FROM context_records ORDER BY d",
                r => DateOnly.ParseExact(r.GetString(0), "yyyy-MM-dd", CultureInfo.InvariantCulture))
            .ToList();
    }

    public HistoryDay LoadDay(DateOnly day)
    {
        var (from, to) = Range(day);
        var args = new[] { ("$from", (object)from), ("$to", to) };

        List<HistoryPoint> points = [];
        using (var db = Open(contextPath))
            if (db is not null)
                points = Query(db, "SELECT id, ts, app, title, domain, decision FROM context_records "
                                   + "WHERE ts >= $from AND ts < $to ORDER BY ts, id",
                    r => new HistoryPoint(r.GetInt64(0), r.GetInt64(1), Str(r, 2), Str(r, 3), Str(r, 4), Str(r, 5) == "save"),
                    args).ToList();

        var shots = new Dictionary<long, HistoryShot>();
        using (var db = Open(screenshotsPath))
            if (db is not null && HasTable(db, "screenshots"))
                foreach (var s in Query(db, "SELECT id, ts, context_record_id FROM screenshots "
                                            + "WHERE ts >= $from AND ts < $to AND context_record_id IS NOT NULL",
                             r => new HistoryShot(r.GetInt64(0), r.GetInt64(1), r.GetInt64(2)), args))
                    shots[s.RecordId] = s;

        var moments = new Dictionary<long, HistoryMoment>();
        using (var db = Open(memoryPath))
            if (db is not null && HasTable(db, "moments"))
                foreach (var m in Query(db, "SELECT doc_id, ts, context_record_id, n_words, fragment, start_word FROM moments "
                                            + "WHERE ts >= $from AND ts < $to AND context_record_id IS NOT NULL",
                             r => new HistoryMoment(r.GetString(0), r.GetInt64(1), r.GetInt64(2), r.GetInt32(3),
                                 r.IsDBNull(4) ? null : r.GetInt32(4), r.IsDBNull(5) ? null : r.GetInt32(5)), args))
                    moments[m.RecordId] = m;

        var buffered = new HashSet<long>();
        using (var db = Open(rawPath))
            if (db is not null && HasTable(db, "raw_captures"))
                buffered.UnionWith(Query(db, "SELECT context_record_id FROM raw_captures WHERE ts >= $from AND ts < $to "
                                             + "AND context_record_id IS NOT NULL AND text_blob IS NOT NULL",
                    r => r.GetInt64(0), args));

        return new HistoryDay(day, points, shots, moments, buffered);
    }

    /// <summary>The screenshot as JPEG bytes, decrypted, or null if it is gone.</summary>
    public byte[]? ShotJpeg(long shotId)
    {
        using var db = Open(screenshotsPath);
        if (db is null) return null;
        var blob = Query(db, "SELECT image_blob FROM screenshots WHERE id = $id", r => (byte[])r.GetValue(0), ("$id", shotId))
            .FirstOrDefault();
        return blob is null ? null : Dpapi.Unprotect(blob, Dpapi.ScreenshotEntropy);
    }

    /// <summary>The stored page, decrypted, or null when it holds no text.</summary>
    public StoredPage? Page(string docId)
    {
        using var db = Open(memoryPath);
        if (db is null) return null;
        var row = Query(db, "SELECT title, fragments_blob FROM documents WHERE doc_id = $id",
            r => (Title: Str(r, 0), Blob: r.IsDBNull(1) ? null : (byte[])r.GetValue(1)), ("$id", docId)).FirstOrDefault();
        if (row.Blob is null) return null;

        using var json = JsonDocument.Parse(Dpapi.UnprotectZlib(row.Blob, Dpapi.TextEntropy));
        var fragments = json.RootElement.GetProperty("fragments").EnumerateArray()
            .Select(f => new PageFragment(
                f.GetProperty("words").EnumerateArray().Select(w => w.GetString() ?? "").ToArray(),
                f.GetProperty("breaks").EnumerateArray().Select(b => b.GetBoolean()).ToArray()))
            .ToList();
        return new StoredPage(docId, row.Title, fragments);
    }

    /// <summary>
    /// The words of the moment, highlighted, with up to <paramref name="context"/> words around them. A moment without a
    /// position shows the start of the page, nothing highlighted.
    /// </summary>
    public static PageExcerpt Excerpt(StoredPage page, HistoryMoment? moment, int context = ExcerptWords)
    {
        if (page.Fragments.Count == 0) return new PageExcerpt("", "", "", false, false);
        if (moment?.Fragment is not { } f || moment.StartWord is not { } start || f >= page.Fragments.Count)
        {
            var first = page.Fragments[0];
            var end = Math.Min(first.Words.Length, 2 * context);
            return new PageExcerpt(Join(first, 0, end), "", "", false, end < first.Words.Length || page.Fragments.Count > 1);
        }
        var fragment = page.Fragments[f];
        var n = fragment.Words.Length;
        start = Math.Clamp(start, 0, n);
        var stop = Math.Clamp(start + moment.Words, start, n);
        var from = Math.Max(0, start - context);
        var to = Math.Min(n, stop + context);
        return new PageExcerpt(Join(fragment, from, start), Join(fragment, start, stop, leading: start > from),
            Join(fragment, stop, to, leading: stop > from), from > 0 || f > 0, to < n || f < page.Fragments.Count - 1);
    }

    /// <summary>Words <paramref name="from"/>..<paramref name="to"/>, separated as on the page. <paramref name="leading"/>
    /// keeps the separator that came before the first word, so consecutive pieces join up exactly.</summary>
    private static string Join(PageFragment fragment, int from, int to, bool leading = false)
    {
        var text = new StringBuilder();
        for (var i = from; i < to; i++)
        {
            if (i > from || leading) text.Append(fragment.Breaks[i] ? '\n' : ' ');
            text.Append(fragment.Words[i]);
        }
        return text.ToString();
    }

    public ScreenshotStats Screenshots()
    {
        using var db = Open(screenshotsPath);
        if (db is null || !HasTable(db, "screenshots")) return ScreenshotStats.Empty;
        return Query(db, "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM screenshots", r => new ScreenshotStats(r.GetInt64(0), r.GetInt64(1)))
            .First();
    }

    /// <summary>Deletes every screenshot and compacts the file so no image lingers in free pages. Returns how many.</summary>
    public long DeleteAllScreenshots()
    {
        using var db = Open(screenshotsPath, readOnly: false);
        if (db is null || !HasTable(db, "screenshots")) return 0;
        var count = Query(db, "SELECT COUNT(*) FROM screenshots", r => r.GetInt64(0)).First();
        foreach (var sql in new[] { "DELETE FROM screenshots", "VACUUM", "PRAGMA wal_checkpoint(TRUNCATE)" })
        {
            using var cmd = db.CreateCommand();
            cmd.CommandText = sql;
            cmd.ExecuteNonQuery();
        }
        return count;
    }

    private static string Str(SqliteDataReader r, int i) => r.IsDBNull(i) ? "" : r.GetString(i);
}

using System.Globalization;
using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Data;

/// <summary>
/// Read-only view of the worker's <c>context.db</c>. The dashboard never writes to it and never
/// holds a connection open: each call opens, reads and closes, so the worker is never blocked and
/// nothing stays allocated between refreshes.
/// </summary>
public sealed class ContextDb
{
    private readonly string _path;

    public ContextDb(string path) => _path = path;

    public string Path => _path;
    public bool Exists => File.Exists(_path);

    private SqliteConnection Open()
    {
        var connection = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = _path,
            Mode = SqliteOpenMode.ReadOnly,
            Pooling = false,
        }.ToString());
        connection.Open();
        return connection;
    }

    public StorageSummary Summary()
    {
        if (!Exists) return StorageSummary.Empty;
        using var db = Open();
        long Scalar(string sql) => Convert.ToInt64(Query(db, sql, r => r.GetValue(0)).FirstOrDefault() ?? 0L, CultureInfo.InvariantCulture);

        var records = Scalar("SELECT COUNT(*) FROM context_records");
        var range = Query(db, "SELECT MIN(ts), MAX(ts) FROM context_records",
            r => (r.IsDBNull(0) ? (long?)null : r.GetInt64(0), r.IsDBNull(1) ? (long?)null : r.GetInt64(1))).First();
        return new StorageSummary(
            records,
            Scalar("SELECT COUNT(*) FROM context_records WHERE decision = 'save'"),
            Scalar("SELECT COUNT(*) FROM context_records WHERE decision = 'discard'"),
            Scalar("SELECT COUNT(*) FROM context_records WHERE embedding IS NOT NULL"),
            Scalar("SELECT COUNT(*) FROM context_records WHERE label IS NOT NULL"),
            Scalar("SELECT COUNT(*) FROM model_versions"),
            range.Item1 is { } first ? FromEpochUs(first) : null,
            range.Item2 is { } last ? FromEpochUs(last) : null,
            SizeOnDisk(),
            (int)Scalar("SELECT COALESCE(MAX(version), 0) FROM schema_migrations"));
    }

    public IReadOnlyList<RecordRow> Recent(int limit, string? appContains = null, string? decision = null, DataScope scope = DataScope.All)
    {
        if (!Exists) return [];
        using var db = Open();
        var hasDrc = HasColumn(db, "context_records", "drc_would_capture");
        var where = new List<string>();
        var parameters = new List<(string, object)>();
        if (!string.IsNullOrWhiteSpace(appContains))
        {
            where.Add("(app LIKE @q OR title LIKE @q OR domain LIKE @q)");
            parameters.Add(("@q", $"%{appContains.Trim()}%"));
        }
        if (decision is "save" or "discard")
        {
            where.Add("decision = @d");
            parameters.Add(("@d", decision));
        }
        var cutoff = SourceCutoff(db);
        where.Add(ScopeSql(scope, cutoff));
        var sourceColumn = cutoff is null ? "'live'" : $"CASE WHEN ts < {cutoff.Value} THEN 'DRC' ELSE 'live' END";
        var drcColumn = hasDrc ? "drc_would_capture" : "NULL";
        var sql = $"SELECT id, ts, app, domain, title, decision, {drcColumn}, {sourceColumn} FROM context_records "
                  + (where.Count > 0 ? "WHERE " + string.Join(" AND ", where) + " " : "")
                  + "ORDER BY ts DESC LIMIT @limit";
        parameters.Add(("@limit", limit));
        return Query(db, sql, r => new RecordRow(
            r.GetInt64(0),
            FromEpochUs(r.GetInt64(1)),
            r.IsDBNull(2) ? "" : r.GetString(2),
            r.IsDBNull(3) ? "" : r.GetString(3),
            r.IsDBNull(4) ? "" : r.GetString(4),
            r.GetString(5),
            r.IsDBNull(6) ? "" : r.GetInt64(6) == 1 ? "capture" : "skip",
            r.GetString(7)), parameters.ToArray());
    }

    public IReadOnlyList<AppStat> TopApps(DateTimeOffset since, int limit = 8)
    {
        if (!Exists) return [];
        using var db = Open();
        return Query(db,
            "SELECT COALESCE(NULLIF(app, ''), '(unknown)'), COUNT(*), SUM(decision = 'save') FROM context_records "
            + "WHERE ts >= @since GROUP BY 1 ORDER BY 2 DESC LIMIT @limit",
            r => new AppStat(r.GetString(0), r.GetInt64(1), r.GetInt64(2)),
            ("@since", ToEpochUs(since)), ("@limit", limit));
    }

    /// <summary>One bucket per local hour for the last <paramref name="hours"/> hours, empty hours included.</summary>
    public IReadOnlyList<HourBucket> Hourly(int hours, DateTimeOffset? now = null)
    {
        var end = (now ?? DateTimeOffset.Now).ToLocalTime();
        var lastHour = new DateTime(end.Year, end.Month, end.Day, end.Hour, 0, 0, DateTimeKind.Local);
        var buckets = Enumerable.Range(0, hours).Select(i => lastHour.AddHours(i - hours + 1)).ToList();
        var saved = new Dictionary<DateTime, long>();
        var discarded = new Dictionary<DateTime, long>();

        if (Exists)
        {
            using var db = Open();
            var since = new DateTimeOffset(buckets[0]);
            foreach (var (ts, decision) in Query(db, "SELECT ts, decision FROM context_records WHERE ts >= @since",
                         r => (FromEpochUs(r.GetInt64(0)).ToLocalTime(), r.GetString(1)), ("@since", ToEpochUs(since))))
            {
                var hour = new DateTime(ts.Year, ts.Month, ts.Day, ts.Hour, 0, 0, DateTimeKind.Local);
                var target = decision == "save" ? saved : discarded;
                target[hour] = target.GetValueOrDefault(hour) + 1;
            }
        }
        return buckets.Select(b => new HourBucket(b, saved.GetValueOrDefault(b), discarded.GetValueOrDefault(b))).ToList();
    }

    /// <summary>One bucket per local day for the last <paramref name="days"/> days, empty days included (<see cref="HourBucket.HourStart"/> is the day's start).</summary>
    public IReadOnlyList<HourBucket> Daily(int days, DateTimeOffset? now = null)
    {
        var end = (now ?? DateTimeOffset.Now).ToLocalTime();
        var today = new DateTime(end.Year, end.Month, end.Day, 0, 0, 0, DateTimeKind.Local);
        var buckets = Enumerable.Range(0, days).Select(i => today.AddDays(i - days + 1)).ToList();
        var saved = new Dictionary<DateTime, long>();
        var discarded = new Dictionary<DateTime, long>();

        if (Exists)
        {
            using var db = Open();
            var since = new DateTimeOffset(buckets[0]);
            foreach (var (ts, decision) in Query(db, "SELECT ts, decision FROM context_records WHERE ts >= @since",
                         r => (FromEpochUs(r.GetInt64(0)).ToLocalTime(), r.GetString(1)), ("@since", ToEpochUs(since))))
            {
                var day = new DateTime(ts.Year, ts.Month, ts.Day, 0, 0, 0, DateTimeKind.Local);
                var target = decision == "save" ? saved : discarded;
                target[day] = target.GetValueOrDefault(day) + 1;
            }
        }
        return buckets.Select(b => new HourBucket(b, saved.GetValueOrDefault(b), discarded.GetValueOrDefault(b))).ToList();
    }

    public IReadOnlyList<ModelRow> Models()
    {
        if (!Exists) return [];
        using var db = Open();
        return Query(db,
            "SELECT version, algorithm, is_active, created_at, metrics_json, notes FROM model_versions ORDER BY created_at DESC",
            r => new ModelRow(
                r.GetString(0),
                r.IsDBNull(1) ? "" : r.GetString(1),
                r.GetInt64(2) == 1,
                FromEpochUs(r.GetInt64(3)),
                r.IsDBNull(4) ? "" : r.GetString(4),
                r.IsDBNull(5) ? "" : r.GetString(5)));
    }

    /// <summary>Agreement with the frozen DRC rules; <see cref="DrcComparison.None"/> before migration v3 or any data.</summary>
    public DrcComparison Drc()
    {
        if (!Exists) return DrcComparison.None;
        using var db = Open();
        if (!HasColumn(db, "context_records", "drc_would_capture")) return DrcComparison.None;
        return Query(db,
            "SELECT COUNT(*), COALESCE(SUM(drc_would_capture = 1), 0), COALESCE(SUM(decision = 'save'), 0), "
            + "COALESCE(SUM(drc_would_capture = 1 AND decision = 'save'), 0) "
            + "FROM context_records WHERE drc_would_capture IS NOT NULL",
            r => new DrcComparison(r.GetInt64(0), r.GetInt64(1), r.GetInt64(2), r.GetInt64(3))).First();
    }

    private static readonly (string Label, double Max)[] DwellEdges =
        [("< 5 s", 5), ("5-15 s", 15), ("15-30 s", 30), ("30-60 s", 60), ("60 s +", double.PositiveInfinity)];

    private const int EngineSampleLimit = 20000;

    /// <summary>
    /// What the Importance Engine page shows. Only stored values are used: the worker does not persist a
    /// per-record importance score or its decision threshold, so there is no score histogram here; dwell time,
    /// revisits and activity are read from each record's feature JSON instead.
    /// <paramref name="scope"/> picks live capture, the merged DRC history, or both. The "last 24 h" and "last 7 days"
    /// windows end at the newest record of that scope, so DRC history (older than the live data) is not empty.
    /// </summary>
    public EngineStats Engine(DateTimeOffset? now = null, DataScope scope = DataScope.All)
    {
        if (!Exists) return EngineStats.Empty;

        using var db = Open();
        long Scalar(string sql, params (string, object)[] p) =>
            Convert.ToInt64(Query(db, sql, r => r.GetValue(0), p).FirstOrDefault() ?? 0L, CultureInfo.InvariantCulture);

        var inScope = ScopeSql(scope, SourceCutoff(db));
        var anchor = now ?? DateTimeOffset.UtcNow;
        if (scope == DataScope.History)
        {
            var newest = Scalar($"SELECT COALESCE(MAX(ts), 0) FROM context_records WHERE {inScope}");
            if (newest > 0) anchor = FromEpochUs(newest);
        }
        var recentFrom = ToEpochUs(anchor.AddHours(-24));
        var priorFrom = ToEpochUs(anchor.AddDays(-8));
        var sampleFrom = ToEpochUs(anchor.AddDays(-7));

        var records = Scalar($"SELECT COUNT(*) FROM context_records WHERE {inScope}");
        var pending = Scalar($"SELECT COUNT(*) FROM context_records WHERE label IS NULL AND {inScope}");

        (long Total, long Saved) Rate(long from, long to) => Query(db,
            $"SELECT COUNT(*), COALESCE(SUM(decision = 'save'), 0) FROM context_records WHERE ts >= @a AND ts < @b AND {inScope}",
            r => (r.GetInt64(0), r.GetInt64(1)), ("@a", from), ("@b", to)).First();
        var recent = Rate(recentFrom, long.MaxValue);
        var prior = Rate(priorFrom, recentFrom);

        var dwell = DwellEdges.Select(_ => (Saved: 0L, Discarded: 0L)).ToArray();
        double revisitSum = 0, noveltySum = 0;
        long n = 0, seen = 0, longDwell = 0, active = 0, novelty = 0;
        foreach (var (decision, json) in Query(db,
                     $"SELECT decision, features_json FROM context_records WHERE ts >= @since AND ts <= @until AND {inScope} ORDER BY ts DESC LIMIT @limit",
                     r => (r.GetString(0), r.GetString(1)),
                     ("@since", sampleFrom), ("@until", ToEpochUs(anchor)), ("@limit", EngineSampleLimit)))
        {
            Dictionary<string, JsonElement>? f;
            try { f = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json); }
            catch (JsonException) { continue; }
            if (f is null) continue;

            n++;
            var seconds = Num(f, "tmp_dwell_seconds");
            var bucket = Array.FindIndex(DwellEdges, e => seconds < e.Max);
            if (decision == "save") dwell[bucket].Saved++; else dwell[bucket].Discarded++;
            if (seconds >= 30) longDwell++;
            revisitSum += Num(f, "ctx_revisit_ratio");
            if (Num(f, "ctx_seen_before") > 0) seen++;
            if (Num(f, "beh_key_presses") + Num(f, "beh_mouse_clicks") + Num(f, "beh_mouse_scrolls") > 0) active++;
            if (Num(f, "vis_has_saved") > 0) { noveltySum += Num(f, "vis_novelty"); novelty++; }
        }

        var signals = n == 0 ? [] : new List<SignalStat>
        {
            new("Revisit ratio", revisitSum / n, "mean share of earlier visits to the same context"),
            new("Seen before", (double)seen / n, "records whose context was seen earlier"),
            new("Dwell 30 s +", (double)longDwell / n, "records the user stayed on for 30 s or more"),
            new("Interaction", (double)active / n, "records with typing, clicks or scrolling"),
            new("Visual novelty", novelty == 0 ? 0 : noveltySum / novelty, novelty == 0 ? "no embeddings yet" : "mean distance from saved screens"),
        };

        var pendingRows = Query(db,
            $"SELECT ts, app, title, decision, features_json FROM context_records WHERE label IS NULL AND {inScope} ORDER BY ts DESC LIMIT 6",
            r =>
            {
                Dictionary<string, JsonElement>? f = null;
                try { f = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(r.GetString(4)); }
                catch (JsonException) { }
                return new PendingRow(
                    FromEpochUs(r.GetInt64(0)),
                    r.IsDBNull(1) ? "" : r.GetString(1),
                    r.IsDBNull(2) ? "" : r.GetString(2),
                    r.GetString(3),
                    f is null ? 0 : Num(f, "tmp_dwell_seconds"),
                    f is null ? 0 : (int)Num(f, "ctx_revisit_count"));
            });

        return new EngineStats(
            records, pending,
            recent.Total == 0 ? null : (double)recent.Saved / recent.Total,
            prior.Total == 0 ? null : (double)prior.Saved / prior.Total,
            (int)n,
            DwellEdges.Select((e, i) => new DwellBucket(e.Label, dwell[i].Saved, dwell[i].Discarded)).ToList(),
            signals, pendingRows);
    }

    /// <summary>
    /// Where the merged DRC history ends and live capture begins. Written by <c>merge_drc_into_live.py</c> as one row in
    /// <c>record_sources</c>: records older than <c>ts_before</c> came from the DesktopResearchCollector.
    /// </summary>
    public DataSources Sources()
    {
        if (!Exists) return DataSources.None;
        using var db = Open();
        var cut = SourceCutoff(db);
        (long Count, DateTimeOffset? From, DateTimeOffset? To) Range(string where) => Query(db,
            $"SELECT COUNT(*), MIN(ts), MAX(ts) FROM context_records WHERE {where}",
            r => (r.GetInt64(0), r.IsDBNull(1) ? (DateTimeOffset?)null : FromEpochUs(r.GetInt64(1)), r.IsDBNull(2) ? (DateTimeOffset?)null : FromEpochUs(r.GetInt64(2)))).First();

        var live = Range(ScopeSql(DataScope.Live, cut));
        var history = Range(ScopeSql(DataScope.History, cut));
        return new DataSources(cut is null ? null : FromEpochUs(cut.Value), history.Count, history.From, history.To, live.Count, live.From, live.To);
    }

    private static long? SourceCutoff(SqliteConnection db)
    {
        if (!Query(db, "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'record_sources'", r => 1).Any()) return null;
        var rows = Query(db, "SELECT ts_before FROM record_sources WHERE source = 'drc'", r => r.GetInt64(0));
        return rows.Count == 0 ? null : rows[0];
    }

    /// <summary>The cutoff is a number read from our own table, so it is safe to put into the SQL text.</summary>
    internal static string ScopeSql(DataScope scope, long? cutoff) => scope switch
    {
        DataScope.Live => cutoff is null ? "1=1" : $"ts >= {cutoff.Value}",
        DataScope.History => cutoff is null ? "1=0" : $"ts < {cutoff.Value}",
        _ => "1=1",
    };

    private static double Num(Dictionary<string, JsonElement> f, string key) =>
        f.TryGetValue(key, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble()
        : v.ValueKind is JsonValueKind.True ? 1 : 0;

    private long SizeOnDisk() =>
        new[] { _path, _path + "-wal", _path + "-shm" }.Where(File.Exists).Sum(p => new FileInfo(p).Length);

    private static bool HasColumn(SqliteConnection db, string table, string column) =>
        Query(db, $"SELECT 1 FROM pragma_table_info('{table}') WHERE name = @c", r => 1, ("@c", column)).Any();

    private static List<T> Query<T>(SqliteConnection db, string sql, Func<SqliteDataReader, T> map, params (string, object)[] parameters)
    {
        using var command = db.CreateCommand();
        command.CommandText = sql;
        foreach (var (name, value) in parameters) command.Parameters.AddWithValue(name, value);
        using var reader = command.ExecuteReader();
        var rows = new List<T>();
        while (reader.Read()) rows.Add(map(reader));
        return rows;
    }

    internal static DateTimeOffset FromEpochUs(long us) => DateTimeOffset.FromUnixTimeMilliseconds(us / 1000);
    internal static long ToEpochUs(DateTimeOffset t) => t.ToUnixTimeMilliseconds() * 1000;
}

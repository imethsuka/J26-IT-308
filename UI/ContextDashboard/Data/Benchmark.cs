using System.Globalization;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Data;

/// <summary>The importance models the worker can run, keyed like <c>model_versions.algorithm</c> (python-worker/models/registry.py).</summary>
public static class Algorithms
{
    public sealed record Info(string Key, string Display, string Family);

    public static readonly IReadOnlyList<Info> All =
    [
        new("lightgbm", "LightGBM (Model 1)", "conventional ML"),
        new("logreg", "Logistic regression", "conventional ML"),
        new("random_forest", "Random forest", "conventional ML"),
        new("mlp", "Neural network (MLP)", "conventional ML"),
        new("mobileclip_logreg", "MobileCLIP only + logistic regression", "transfer learning"),
        new("features_mobileclip_logreg", "Features + MobileCLIP + logistic regression", "transfer learning"),
        new("river_arf", "River adaptive random forest", "continual learning"),
        new("rules", "Rules", "baseline"),
    ];

    public static Info Of(string key) => All.FirstOrDefault(a => a.Key == key) ?? new(key, key, "");
}

/// <summary>One model family on the benchmark page: what it did live and how it is graded once labels arrive.</summary>
public sealed record BenchmarkRow(
    string Algorithm,
    string Display,
    string Family,
    string? LatestVersion,
    bool IsChampion,
    long Scored,
    long Scored24h,
    double? KeepRate24h,
    double? Agreement24h,
    long Graded,
    double? LiveAp,
    double? LiveRecall30,
    double? LatencyMs)
{
    public string Role => IsChampion ? "champion" : "shadow";
    /// <summary>The rules have no saved version, and the champion is already champion.</summary>
    public bool CanBeChampion => !IsChampion && LatestVersion is not null && Algorithm != "rules";
    public string KeepRateText => KeepRate24h is { } v ? v.ToString("P0", CultureInfo.CurrentCulture) : "–";
    public string AgreementText => IsChampion ? "–" : Agreement24h is { } v ? v.ToString("P0", CultureInfo.CurrentCulture) : "–";
    public string ApText => LiveAp is { } v ? v.ToString("0.000", CultureInfo.InvariantCulture) : "waiting";
    public string RecallText => LiveRecall30 is { } v ? v.ToString("P0", CultureInfo.CurrentCulture) : "–";
    public string LatencyText => LatencyMs is { } v ? v.ToString(v < 10 ? "0.00" : "0.0", CultureInfo.InvariantCulture) : "–";
}

/// <summary>One line on a chart: a value per local day.</summary>
public sealed record BenchmarkSeries(string Name, IReadOnlyList<(DateTime Day, double Value)> Points);

public sealed record ChampionChange(DateTimeOffset At, string? From, string To, string Reason);

public sealed record BenchmarkData(
    IReadOnlyList<BenchmarkRow> Rows,
    IReadOnlyList<BenchmarkSeries> KeepRateByDay,
    IReadOnlyList<BenchmarkSeries> ApByDay,
    IReadOnlyList<ChampionChange> Changes,
    long GradedScreens,
    double? BaseRate,
    DateTimeOffset? FirstDecision)
{
    public static readonly BenchmarkData Empty = new([], [], [], [], 0, null, null);
    public bool Available => Rows.Count > 0;
}

/// <summary>Threshold-free grades, computed the same way as python-worker (sklearn's average precision).</summary>
public static class Ranking
{
    /// <summary>Average precision: precision at each recall step, ties scored together. Null without both classes.</summary>
    public static double? AveragePrecision(IReadOnlyList<(double Score, int Label)> rows)
    {
        var positives = rows.Count(r => r.Label == 1);
        if (positives == 0 || positives == rows.Count) return null;
        var ordered = rows.OrderByDescending(r => r.Score).ToList();
        double ap = 0, previousRecall = 0;
        int tp = 0, fp = 0;
        for (var i = 0; i < ordered.Count;)
        {
            var score = ordered[i].Score;
            for (; i < ordered.Count && ordered[i].Score == score; i++)
                if (ordered[i].Label == 1) tp++; else fp++;
            var recall = (double)tp / positives;
            ap += (recall - previousRecall) * tp / (tp + fp);
            previousRecall = recall;
        }
        return ap;
    }

    /// <summary>Share of the important contexts among the top <paramref name="budget"/> by score.</summary>
    public static double? RecallAtBudget(IReadOnlyList<(double Score, int Label)> rows, double budget)
    {
        var positives = rows.Count(r => r.Label == 1);
        if (positives == 0) return null;
        var keep = (int)Math.Round(budget * rows.Count);
        return (double)rows.OrderByDescending(r => r.Score).Take(keep).Count(r => r.Label == 1) / positives;
    }
}

public sealed partial class ContextDb
{
    /// <summary>
    /// Shadow-mode benchmark (schema v4 <c>model_decisions</c>): per model family, what it scored and would have saved
    /// live, how often it agreed with the champion, and its live grades on the contexts labelled since (the last
    /// <paramref name="days"/> days). Grades are by family, so a family keeps its line when a newer version takes over.
    /// </summary>
    public BenchmarkData Benchmark(DateTimeOffset? now = null, int days = 30)
    {
        if (!Exists) return BenchmarkData.Empty;
        using var db = Open();
        if (!Query(db, "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'model_decisions'", r => 1).Any())
            return BenchmarkData.Empty;

        var nowUs = ToEpochUs(now ?? DateTimeOffset.UtcNow);
        var dayAgo = nowUs - 24L * 3600 * 1_000_000;
        var since = nowUs - days * 24L * 3600 * 1_000_000;
        var offsetS = (long)TimeZoneInfo.Local.GetUtcOffset(now ?? DateTimeOffset.UtcNow).TotalSeconds;

        var counts = Query(db,
            "SELECT algorithm, COUNT(*), COALESCE(SUM(ts >= @d), 0), AVG(CASE WHEN ts >= @d THEN would_save END), AVG(latency_us) "
            + "FROM model_decisions GROUP BY algorithm",
            r => (Alg: r.GetString(0), All: r.GetInt64(1), Day: r.GetInt64(2), Keep: r.IsDBNull(3) ? (double?)null : r.GetDouble(3),
                  LatencyMs: r.IsDBNull(4) ? (double?)null : r.GetDouble(4) / 1000.0),
            ("@d", dayAgo)).ToDictionary(c => c.Alg);
        if (counts.Count == 0) return BenchmarkData.Empty;

        var agreement = Query(db,
            "SELECT s.algorithm, AVG(s.would_save = c.would_save) FROM model_decisions s "
            + "JOIN model_decisions c ON c.record_id = s.record_id AND c.role = 'champion' "
            + "WHERE s.role = 'shadow' AND s.ts >= @d GROUP BY s.algorithm",
            r => (r.GetString(0), r.GetDouble(1)), ("@d", dayAgo)).ToDictionary(a => a.Item1, a => a.Item2);

        // The newest version of each family, and the champion (no active version = the rules decide).
        var latest = new Dictionary<string, string>();
        string? championVersion = null, championAlgorithm = null;
        foreach (var (version, algorithm, active) in Query(db,
                     "SELECT version, COALESCE(algorithm, 'lightgbm'), is_active FROM model_versions ORDER BY created_at, id",
                     r => (r.GetString(0), r.GetString(1), r.GetInt64(2) == 1)))
        {
            latest[algorithm] = version;
            if (active) (championVersion, championAlgorithm) = (version, algorithm);
        }
        championAlgorithm ??= "rules";

        var graded = new Dictionary<string, List<(double Score, int Label)>>();
        var gradedByDay = new Dictionary<(string, DateTime), List<(double, int)>>();
        foreach (var (alg, ts, score, label) in Query(db,
                     "SELECT d.algorithm, d.ts, d.score, r.label FROM model_decisions d JOIN context_records r ON r.id = d.record_id "
                     + "WHERE r.label IS NOT NULL AND d.ts >= @s",
                     r => (r.GetString(0), r.GetInt64(1), r.GetDouble(2), (int)r.GetInt64(3)), ("@s", since)))
        {
            (graded.TryGetValue(alg, out var list) ? list : graded[alg] = []).Add((score, label));
            var day = FromEpochUs(ts).ToLocalTime().Date;
            (gradedByDay.TryGetValue((alg, day), out var dayList) ? dayList : gradedByDay[(alg, day)] = []).Add((score, label));
        }

        var rows = new List<BenchmarkRow>();
        foreach (var info in Algorithms.All)
        {
            if (!counts.TryGetValue(info.Key, out var c) && !latest.ContainsKey(info.Key)) continue;
            var g = graded.GetValueOrDefault(info.Key) ?? [];
            rows.Add(new BenchmarkRow(
                info.Key, info.Display, info.Family, info.Key == "rules" ? null : latest.GetValueOrDefault(info.Key),
                info.Key == championAlgorithm, c.All, c.Day, c.Keep, agreement.TryGetValue(info.Key, out var a) ? a : null,
                g.Count, Ranking.AveragePrecision(g), Ranking.RecallAtBudget(g, 0.3), c.LatencyMs));
        }
        rows = rows.OrderByDescending(r => r.IsChampion).ThenByDescending(r => r.LiveAp ?? -1).ThenBy(r => r.Display).ToList();

        var keepByDay = Query(db,
            "SELECT algorithm, (ts / 1000000 + @o) / 86400, AVG(would_save), COUNT(*) FROM model_decisions WHERE ts >= @s GROUP BY 1, 2",
            r => (Alg: r.GetString(0), Day: DateTime.UnixEpoch.AddDays(r.GetInt64(1)), Keep: r.GetDouble(2)),
            ("@o", offsetS), ("@s", since));
        var keepSeries = rows.Select(row => new BenchmarkSeries(row.Display,
            keepByDay.Where(k => k.Alg == row.Algorithm).OrderBy(k => k.Day).Select(k => (k.Day, k.Keep)).ToList())).ToList();
        var apSeries = rows.Select(row => new BenchmarkSeries(row.Display,
            gradedByDay.Where(kv => kv.Key.Item1 == row.Algorithm && kv.Value.Count >= 20)
                .Select(kv => (Day: kv.Key.Item2, Ap: Ranking.AveragePrecision(kv.Value)))
                .Where(p => p.Ap is not null).OrderBy(p => p.Day).Select(p => (p.Day, p.Ap!.Value)).ToList())).ToList();

        var changes = Query(db,
            "SELECT ts, from_version, to_version, COALESCE(reason, '') FROM champion_events ORDER BY ts DESC LIMIT 50",
            r => new ChampionChange(FromEpochUs(r.GetInt64(0)), r.IsDBNull(1) ? null : r.GetString(1), r.GetString(2), r.GetString(3)));
        var baseRate = Query(db,
            "SELECT AVG(label), COUNT(*) FROM context_records WHERE label IS NOT NULL "
            + "AND id IN (SELECT record_id FROM model_decisions WHERE ts >= @s)",
            r => (Rate: r.IsDBNull(0) ? (double?)null : r.GetDouble(0), Count: r.GetInt64(1)), ("@s", since)).First();
        var first = Query(db, "SELECT MIN(ts) FROM model_decisions", r => r.IsDBNull(0) ? (long?)null : r.GetInt64(0)).First();

        return new BenchmarkData(rows, keepSeries, apSeries, changes, baseRate.Count, baseRate.Rate,
            first is { } f ? FromEpochUs(f) : null);
    }
}

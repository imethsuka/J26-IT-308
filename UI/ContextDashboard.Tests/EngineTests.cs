using ContextDashboard.Data;

namespace ContextDashboard.Tests;

/// <summary>The Importance Engine page reads only stored values: dwell buckets, signals, commit rate, pending labels.</summary>
public class EngineTests
{
    private static readonly DateTimeOffset Now = new(2026, 10, 21, 12, 0, 0, TimeSpan.Zero);

    private static string F(double dwell, double revisitRatio = 0, int seen = 0, int keys = 0, int revisits = 0) =>
        $"{{\"tmp_dwell_seconds\":{dwell},\"ctx_revisit_ratio\":{revisitRatio},\"ctx_seen_before\":{seen},"
        + $"\"beh_key_presses\":{keys},\"beh_mouse_clicks\":0,\"beh_mouse_scrolls\":0,\"ctx_revisit_count\":{revisits}}}";

    [Fact]
    public void Missing_database_is_empty()
    {
        var db = new ContextDb(Path.Combine(Path.GetTempPath(), $"nope-{Guid.NewGuid():N}.db"));
        Assert.Equal(EngineStats.Empty, db.Engine(Now));
    }

    [Fact]
    public void Dwell_buckets_split_saved_and_discarded()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddHours(-1), "Code", "save", features: F(3));
        t.AddRecord(Now.AddHours(-2), "Code", "discard", features: F(4));
        t.AddRecord(Now.AddHours(-3), "Edge", "save", features: F(20));
        t.AddRecord(Now.AddHours(-4), "Edge", "save", features: F(120));

        var e = new ContextDb(t.Path).Engine(Now);

        Assert.Equal(4, e.SampleSize);
        Assert.Equal(new[] { "< 5 s", "5-15 s", "15-30 s", "30-60 s", "60 s +" }, e.Dwell.Select(b => b.Label));
        Assert.Equal((1L, 1L), (e.Dwell[0].Saved, e.Dwell[0].Discarded));
        Assert.Equal((1L, 0L), (e.Dwell[2].Saved, e.Dwell[2].Discarded));
        Assert.Equal((1L, 0L), (e.Dwell[4].Saved, e.Dwell[4].Discarded));
        Assert.Equal(4, e.Dwell.Sum(b => b.Total));
    }

    [Fact]
    public void Signals_are_averages_of_the_stored_features()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddHours(-1), "Code", "save", features: F(40, revisitRatio: 0.5, seen: 1, keys: 5));
        t.AddRecord(Now.AddHours(-2), "Code", "save", features: F(10, revisitRatio: 0.1, seen: 0, keys: 0));

        var signals = new ContextDb(t.Path).Engine(Now).Signals.ToDictionary(s => s.Name, s => s.Value);

        Assert.Equal(0.3, signals["Revisit ratio"], 3);
        Assert.Equal(0.5, signals["Seen before"], 3);
        Assert.Equal(0.5, signals["Dwell 30 s +"], 3);
        Assert.Equal(0.5, signals["Interaction"], 3);
    }

    [Fact]
    public void Commit_rate_compares_the_last_day_with_the_week_before()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddHours(-2), "Code", "save");
        t.AddRecord(Now.AddHours(-3), "Code", "discard");                 // last 24 h: 1 of 2
        for (var i = 0; i < 4; i++) t.AddRecord(Now.AddDays(-3).AddHours(-i), "Code", i == 0 ? "save" : "discard");   // before: 1 of 4

        var e = new ContextDb(t.Path).Engine(Now);

        Assert.Equal(0.5, e.CommitRateRecent!.Value, 3);
        Assert.Equal(0.25, e.CommitRatePrior!.Value, 3);
        Assert.Equal(0.25, e.Shift!.Value, 3);
    }

    [Fact]
    public void Pending_counts_unlabelled_records_and_lists_the_newest()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddHours(-5), "Old", "save", label: 1);
        t.AddRecord(Now.AddHours(-2), "Mid", "discard", features: F(7, revisits: 2));
        t.AddRecord(Now.AddHours(-1), "New", "save", features: F(90, revisits: 3));

        var e = new ContextDb(t.Path).Engine(Now);

        Assert.Equal(3, e.Records);
        Assert.Equal(2, e.Pending);
        Assert.Equal(new[] { "New", "Mid" }, e.PendingNewest.Select(p => p.App));
        Assert.Equal(3, e.PendingNewest[0].Revisits);
        Assert.Equal(90, e.PendingNewest[0].DwellSeconds);
    }

    [Fact]
    public void Unparseable_feature_json_is_skipped_not_fatal()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddHours(-1), "Code", "save", features: "not json");
        t.AddRecord(Now.AddHours(-2), "Code", "save", features: F(10));

        var e = new ContextDb(t.Path).Engine(Now);

        Assert.Equal(1, e.SampleSize);
        Assert.Equal(2, e.Records);
    }
}

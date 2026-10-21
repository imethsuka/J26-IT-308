using ContextDashboard.Data;

namespace ContextDashboard.Tests;

/// <summary>The activity chart's 7 days / 30 days / 3 months views: one bar per local day, empty days included.</summary>
public class DailyActivityTests
{
    private static readonly DateTimeOffset Now = new(2026, 10, 21, 15, 0, 0, TimeSpan.Zero);

    [Fact]
    public void Daily_returns_every_day_including_empty_ones_oldest_first()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddHours(-1), "Code", "save");
        t.AddRecord(Now.AddHours(-2), "Code", "discard");
        t.AddRecord(Now.AddDays(-3), "Edge", "save");

        var days = new ContextDb(t.Path).Daily(7, Now);

        Assert.Equal(7, days.Count);
        Assert.True(days[0].HourStart < days[^1].HourStart);
        Assert.All(days.Zip(days.Skip(1)), p => Assert.Equal(1.0, (p.Second.HourStart - p.First.HourStart).TotalDays, 1));
        Assert.Equal(3, days.Sum(d => d.Total));
        Assert.Equal(2, days.Sum(d => d.Saved));
        Assert.Contains(days, d => d.Total == 0);
    }

    [Fact]
    public void Daily_ignores_records_older_than_the_window()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddDays(-40), "Old", "save");
        t.AddRecord(Now.AddDays(-2), "New", "save");

        Assert.Equal(1, new ContextDb(t.Path).Daily(30, Now).Sum(d => d.Total));
        Assert.Equal(2, new ContextDb(t.Path).Daily(90, Now).Sum(d => d.Total));
    }

    [Fact]
    public void Daily_on_a_missing_database_is_all_empty_days()
    {
        var db = new ContextDb(Path.Combine(Path.GetTempPath(), $"nope-{Guid.NewGuid():N}.db"));
        var days = db.Daily(30, Now);

        Assert.Equal(30, days.Count);
        Assert.All(days, d => Assert.Equal(0, d.Total));
    }
}

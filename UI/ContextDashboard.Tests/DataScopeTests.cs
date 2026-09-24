using ContextDashboard.Data;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Tests;

/// <summary>Live capture versus the DRC history merged in by merge_drc_into_live.py (cutoff stored in record_sources).</summary>
public class DataScopeTests
{
    private static readonly DateTimeOffset Cut = new(2026, 10, 19, 13, 0, 0, TimeSpan.Zero);

    private static TempDb WithHistory(int history, int live, bool register = true)
    {
        var t = new TempDb();
        for (var i = 0; i < history; i++) t.AddRecord(Cut.AddHours(-1 - i), "Hist", i % 2 == 0 ? "save" : "discard");
        for (var i = 0; i < live; i++) t.AddRecord(Cut.AddMinutes(1 + i), "Live", "save");
        if (register)
        {
            using var c = new SqliteConnection($"Data Source={t.Path};Pooling=False");
            c.Open();
            using var cmd = c.CreateCommand();
            cmd.CommandText = "CREATE TABLE record_sources (source TEXT PRIMARY KEY, ts_before INTEGER NOT NULL, record_count INTEGER NOT NULL, imported_at INTEGER NOT NULL, note TEXT);"
                              + $"INSERT INTO record_sources VALUES ('drc', {ContextDb.ToEpochUs(Cut)}, {history}, 0, 'x');";
            cmd.ExecuteNonQuery();
        }
        return t;
    }

    [Fact]
    public void Sources_splits_history_from_live_at_the_cutoff()
    {
        using var t = WithHistory(history: 4, live: 3);
        var s = new ContextDb(t.Path).Sources();

        Assert.Equal(4, s.HistoryCount);
        Assert.Equal(3, s.LiveCount);
        Assert.Equal(Cut, s.Cutoff);
        Assert.True(s.HasHistory);
        Assert.True(s.HistoryTo < Cut && s.LiveFrom > Cut);
    }

    [Fact]
    public void Without_a_merge_everything_is_live()
    {
        using var t = WithHistory(history: 0, live: 5, register: false);
        var s = new ContextDb(t.Path).Sources();

        Assert.Equal(5, s.LiveCount);
        Assert.False(s.HasHistory);
        Assert.Null(s.Cutoff);
    }

    [Fact]
    public void Engine_scope_covers_only_its_own_records()
    {
        using var t = WithHistory(history: 4, live: 3);
        var db = new ContextDb(t.Path);

        Assert.Equal(7, db.Engine(Cut.AddDays(1), DataScope.All).Records);
        Assert.Equal(3, db.Engine(Cut.AddDays(1), DataScope.Live).Records);
        Assert.Equal(4, db.Engine(Cut.AddDays(1), DataScope.History).Records);
    }

    [Fact]
    public void History_windows_end_at_the_newest_history_record_not_today()
    {
        using var t = WithHistory(history: 4, live: 3);

        // "now" is far after the history ended: a today-anchored window would be empty.
        var history = new ContextDb(t.Path).Engine(Cut.AddDays(30), DataScope.History);

        Assert.Equal(4, history.SampleSize);
        Assert.NotNull(history.CommitRateRecent);
    }

    [Fact]
    public void Recent_filters_by_scope_and_labels_each_row_with_its_source()
    {
        using var t = WithHistory(history: 2, live: 2);
        var db = new ContextDb(t.Path);

        Assert.All(db.Recent(50, scope: DataScope.History), r => Assert.Equal("DRC", r.Source));
        Assert.All(db.Recent(50, scope: DataScope.Live), r => Assert.Equal("live", r.Source));
        Assert.Equal(4, db.Recent(50).Count);
    }

    [Fact]
    public void History_scope_is_empty_when_nothing_was_merged()
    {
        using var t = WithHistory(history: 0, live: 3, register: false);
        var db = new ContextDb(t.Path);

        Assert.Empty(db.Recent(50, scope: DataScope.History));
        Assert.Equal(0, db.Engine(null, DataScope.History).Records);
    }
}

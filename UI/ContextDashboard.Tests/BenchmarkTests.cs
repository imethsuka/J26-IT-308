using ContextDashboard.Data;
using ContextDashboard.Services;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Tests;

public class BenchmarkTests
{
    private static readonly DateTimeOffset Now = new(2026, 10, 25, 12, 0, 0, TimeSpan.Zero);

    [Fact]
    public void Average_precision_matches_sklearn_including_ties()
    {
        Assert.Equal(0.75, Ranking.AveragePrecision([(0.9, 1), (0.1, 1), (0.8, 0), (0.2, 0)])!.Value, 6);
        Assert.Equal(0.755556, Ranking.AveragePrecision([(0.5, 1), (0.5, 0), (0.4, 1), (0.4, 0), (0.9, 1)])!.Value, 6);
        Assert.Null(Ranking.AveragePrecision([(0.5, 1), (0.4, 1)]));  // one class only
        Assert.Equal(0.5, Ranking.RecallAtBudget([(0.9, 1), (0.1, 1), (0.8, 0), (0.2, 0)], 0.5));
    }

    [Fact]
    public void A_database_before_shadow_mode_reads_as_empty()
    {
        using var t = new TempDb();
        t.AddRecord(Now, "Code", "save");
        Assert.False(new ContextDb(t.Path).Benchmark(Now).Available);
    }

    [Fact]
    public void Benchmark_grades_each_family_on_its_recorded_scores()
    {
        using var t = new TempDb();
        ShadowTables(t);
        Exec(t, "INSERT INTO model_versions (version, algorithm, created_at, is_active) VALUES ('lightgbm-a', 'lightgbm', 1, 1), ('logreg-a', 'logreg', 2, 0)");
        // Four labelled screens two days ago: LightGBM ranks them perfectly, logistic regression backwards.
        var old = Now.AddDays(-2);
        int[] labels = [1, 1, 0, 0];
        for (var i = 0; i < 4; i++)
        {
            t.AddRecord(old.AddMinutes(i), "Code", "save", label: labels[i]);
            Decision(t, i + 1, "lightgbm-lightgbm-a", "lightgbm", "champion", old.AddMinutes(i), 0.9 - 0.2 * i, labels[i] == 1);
            Decision(t, i + 1, "logreg-logreg-a", "logreg", "shadow", old.AddMinutes(i), 0.1 + 0.2 * i, labels[i] == 0);
        }
        // Two unlabelled screens in the last day: they disagree on one.
        for (var i = 0; i < 2; i++)
        {
            t.AddRecord(Now.AddHours(-1).AddMinutes(i), "msedge", "discard");
            Decision(t, 5 + i, "lightgbm-lightgbm-a", "lightgbm", "champion", Now.AddHours(-1).AddMinutes(i), 0.2, false);
            Decision(t, 5 + i, "logreg-logreg-a", "logreg", "shadow", Now.AddHours(-1).AddMinutes(i), 0.6, i == 0);
        }
        Exec(t, $"INSERT INTO champion_events (ts, from_version, to_version, algorithm, reason) VALUES ({ContextDb.ToEpochUs(old)}, NULL, 'lightgbm-a', 'lightgbm', 'deploy gate')");

        var b = new ContextDb(t.Path).Benchmark(Now);

        Assert.True(b.Available);
        var champion = b.Rows[0];
        Assert.Equal("lightgbm", champion.Algorithm);
        Assert.True(champion.IsChampion);
        Assert.Equal(1.0, champion.LiveAp!.Value, 6);
        Assert.Equal(4, champion.Graded);
        var logreg = b.Rows.Single(r => r.Algorithm == "logreg");
        Assert.True(logreg.CanBeChampion);
        Assert.Equal("logreg-a", logreg.LatestVersion);
        Assert.True(logreg.LiveAp < 0.6);
        Assert.Equal(2, logreg.Scored24h);
        Assert.Equal(0.5, logreg.KeepRate24h);
        Assert.Equal(0.5, logreg.Agreement24h);   // agrees on the one it would discard
        Assert.Equal(4, b.GradedScreens);
        Assert.Equal(0.5, b.BaseRate);
        Assert.Single(b.Changes);
        Assert.Equal(2, b.KeepRateByDay.Count);
    }

    [Theory]
    [InlineData("{\"champion\": \"logreg-a\", \"changed\": true, \"previous\": \"lightgbm-a\", \"algorithm\": \"logreg\"}", true, true)]
    [InlineData("{\"champion\": \"logreg-a\", \"changed\": false, \"previous\": \"logreg-a\"}", true, false)]
    [InlineData("{\"error\": \"'x' cannot be loaded; the champion is unchanged\"}", false, false)]
    [InlineData("Traceback (most recent call last):", false, false)]
    public void Champion_command_output_is_parsed(string stdout, bool ok, bool changed)
    {
        var result = ChampionSwitch.Parse("some log line\n" + stdout + "\n", "", ok ? 0 : 1);
        Assert.Equal(ok, result.Ok);
        Assert.Equal(changed, result.Changed);
        Assert.False(string.IsNullOrWhiteSpace(result.Message));
    }

    private static void ShadowTables(TempDb t) => Exec(t, @"
        CREATE TABLE model_decisions (record_id INTEGER NOT NULL, model TEXT NOT NULL, algorithm TEXT NOT NULL, model_version_id INTEGER,
            role TEXT NOT NULL, ts INTEGER NOT NULL, score REAL NOT NULL, cutoff REAL, would_save INTEGER NOT NULL, latency_us INTEGER,
            PRIMARY KEY (record_id, model));
        CREATE TABLE champion_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, from_version TEXT, to_version TEXT NOT NULL,
            algorithm TEXT, reason TEXT);");

    private static void Decision(TempDb t, int record, string model, string algorithm, string role, DateTimeOffset ts, double score, bool save) =>
        Exec(t, "INSERT INTO model_decisions (record_id, model, algorithm, role, ts, score, cutoff, would_save, latency_us) VALUES "
                + $"({record}, '{model}', '{algorithm}', '{role}', {ContextDb.ToEpochUs(ts)}, {score.ToString(System.Globalization.CultureInfo.InvariantCulture)}, 0.5, {(save ? 1 : 0)}, 250)");

    private static void Exec(TempDb t, string sql)
    {
        using var c = new SqliteConnection($"Data Source={t.Path};Pooling=False");
        c.Open();
        using var cmd = c.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }
}

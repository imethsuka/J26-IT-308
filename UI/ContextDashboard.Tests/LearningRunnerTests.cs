using ContextDashboard.Data;
using ContextDashboard.Services;
using ContextDashboard.Views;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Tests;

/// <summary>Run-log lines below are real output of <c>adaptation.scheduled_job</c> (trimmed), not invented.</summary>
public class LearningRunnerTests
{
    private const string Keep =
        "{\"started_at\":\"2026-10-20T05:16:59.000000+00:00\",\"ran\":true,\"skip_reason\":null,\"decision\":\"keep\"," +
        "\"candidate_version\":\"lightgbm-20261020-051659\",\"contexts_labelled\":270,\"stages\":{\"label_construction\":{}," +
        "\"validation\":{\"deployed\":false,\"reason\":\"candidate f1 0.7529 not strictly better than current 0.7733\"}}," +
        "\"error\":null,\"finished_at\":\"2026-10-20T05:17:01.874228+00:00\"}";

    private const string Deploy =
        "{\"ran\":true,\"skip_reason\":null,\"decision\":\"deploy\",\"candidate_version\":\"lightgbm-20261021-010000\"," +
        "\"contexts_labelled\":900,\"stages\":{\"validation\":{\"deployed\":true,\"reason\":\"candidate f1 0.81 beats rules-1.0 0.74\"}}," +
        "\"error\":null,\"finished_at\":\"2026-10-21T01:00:05+00:00\"}";

    private const string Skipped =
        "{\"ran\":true,\"skip_reason\":null,\"decision\":\"skipped\",\"candidate_version\":null,\"contexts_labelled\":0," +
        "\"stages\":{\"retrain\":{\"reason\":\"need >= 20 labelled rows across both classes; have 0 rows, classes []\"}}," +
        "\"error\":null,\"finished_at\":\"2026-10-20T06:00:00+00:00\"}";

    private static string Write(params string[] lines)
    {
        var path = Path.Combine(Path.GetTempPath(), $"runs-{Guid.NewGuid():N}.jsonl");
        File.WriteAllText(path, string.Join("\n", lines) + "\n");
        return path;
    }

    [Fact]
    public void A_kept_candidate_is_explained_in_plain_words()
    {
        var path = Write(Keep);
        try
        {
            var r = LearningRunner.ParseLastRun(path);
            Assert.False(r.Deployed);
            Assert.Equal(270, r.Labelled);
            Assert.Equal("lightgbm-20261020-051659", r.Candidate);
            Assert.Contains("270 records labelled", r.Summary);
            Assert.Contains("current model stays", r.Summary);
            Assert.Contains("not strictly better", r.Summary);
        }
        finally { File.Delete(path); }
    }

    [Fact]
    public void A_deployed_model_is_reported_as_deployed()
    {
        var path = Write(Keep, Deploy);
        try
        {
            var r = LearningRunner.ParseLastRun(path);
            Assert.True(r.Deployed);
            Assert.Contains("a new model was deployed", r.Summary);
            Assert.Contains("beats rules-1.0", r.Summary);
        }
        finally { File.Delete(path); }
    }

    [Fact]
    public void A_skipped_run_says_why_nothing_was_trained()
    {
        var path = Write(Skipped);
        try
        {
            var r = LearningRunner.ParseLastRun(path);
            Assert.Equal("skipped", r.Decision);
            Assert.Contains("nothing was trained", r.Summary);
            Assert.Contains("need >= 20 labelled rows", r.Summary);
        }
        finally { File.Delete(path); }
    }

    [Fact]
    public void The_newest_line_wins_and_bad_input_is_harmless()
    {
        var path = Write(Deploy, Keep);
        try { Assert.False(LearningRunner.ParseLastRun(path).Deployed); }
        finally { File.Delete(path); }

        var garbage = Write("not json at all");
        try { Assert.Same(LearningResult.None, LearningRunner.ParseLastRun(garbage)); }
        finally { File.Delete(garbage); }

        Assert.Same(LearningResult.None, LearningRunner.ParseLastRun(Path.Combine(Path.GetTempPath(), "missing.jsonl")));
    }

    [Fact]
    public void An_error_in_the_log_is_shown_as_a_failure()
    {
        var path = Write("{\"ran\":true,\"decision\":\"skipped\",\"contexts_labelled\":0,\"error\":\"disk full\",\"finished_at\":\"2026-10-20T06:00:00+00:00\"}");
        try { Assert.Contains("failed. disk full", LearningRunner.ParseLastRun(path).Summary); }
        finally { File.Delete(path); }
    }

    [Fact]
    public void Installed_app_runs_adapt_exe_next_to_the_worker()
    {
        var settings = new AppSettings { WorkerExe = @"C:\app\worker\worker.exe", DbPath = @"C:\d\context.db" };
        var runner = new LearningRunner(settings);

        var (file, args) = runner.Command(quick: false, labelRule: "v3");
        Assert.Equal(@"C:\app\worker\adapt.exe", file);
        Assert.Contains("--force --no-require-ac", args);
        Assert.DoesNotContain("--label-horizon-hours", args);
        Assert.DoesNotContain("--label-rule", args);
        Assert.Contains($"--db \"{settings.DbPath}\"", args);

        var quick = runner.Command(quick: true, labelRule: "kw").Args;
        Assert.Contains("--label-delay-hours 1 --label-horizon-hours 1", quick);
        Assert.Contains("--label-rule kw", quick);
    }

    [Fact]
    public void The_background_learner_waits_for_an_idle_pc_instead_of_forcing_a_run()
    {
        var runner = new LearningRunner(new AppSettings { WorkerExe = @"C:\app\worker\worker.exe", DbPath = @"C:\d\context.db" });
        var args = runner.Command(false, "v3", daemon: true).Args;
        Assert.Contains("--loop", args);
        Assert.DoesNotContain("--force", args);
        Assert.DoesNotContain("--no-require-ac", args);  // keeps the AC-power requirement: no battery drain
    }

    [Fact]
    public void From_the_repo_it_runs_the_module_with_python()
    {
        var runner = new LearningRunner(new AppSettings { PythonExe = @"C:\py\python.exe", DbPath = @"C:\d\context.db" });
        var (file, args) = runner.Command(false, "v3");
        Assert.Equal(@"C:\py\python.exe", file);
        Assert.StartsWith("-m adaptation.scheduled_job", args);
    }

    [Fact]
    public void Problems_are_reported_before_anything_runs()
    {
        var runner = new LearningRunner(new AppSettings { WorkerExe = @"C:\nope\worker.exe", DbPath = @"C:\nope\x.db" });
        Assert.Contains("Learning job not found", runner.Problem());
    }

    [Fact]
    public void Backup_is_a_consistent_copy_and_only_the_newest_few_are_kept()
    {
        using var t = new TempDb();
        t.AddRecord(DateTimeOffset.UtcNow, "Code", "save");
        var dir = Path.Combine(Path.GetTempPath(), $"bk-{Guid.NewGuid():N}");
        try
        {
            var runner = new LearningRunner(new AppSettings { DbPath = t.Path }, dir);
            for (var i = 0; i < 5; i++)
            {
                runner.Backup();
                Thread.Sleep(1100);  // file names carry the second
            }
            var backups = new DirectoryInfo(dir).GetFiles("context-*.db");
            Assert.Equal(3, backups.Length);
            Assert.Equal(1, new ContextDb(backups[0].FullName).Summary().Records);
        }
        finally
        {
            SqliteConnection.ClearAllPools();
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
        }
    }
}


/// <summary>Runs the real frozen job (packaging\\dist\\worker\\adapt.exe) on a throw-away database. Skipped when it is not built.</summary>
public class LearningRunnerIntegrationTests
{
    private static string? FindAdapt()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "packaging", "dist", "worker", "adapt.exe");
            if (File.Exists(candidate)) return candidate;
        }
        return null;
    }

    [Fact]
    public async Task One_manual_run_backs_up_labels_trains_and_reports()
    {
        var adapt = FindAdapt();
        if (adapt is null) return;  // frozen worker not built on this machine

        var root = Path.Combine(Path.GetTempPath(), $"learn-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var db = Path.Combine(root, "t.db");
        // A frozen job keeps its trained model versions in the user's data folder. Point that at the temp folder
        // so the test can never write a model into the real %LOCALAPPDATA%\\context-importance.
        const string DataEnv = "CONTEXT_IMPORTANCE_DATA";
        var previous = Environment.GetEnvironmentVariable(DataEnv);
        Environment.SetEnvironmentVariable(DataEnv, root);
        try
        {
            // Let the job create and migrate the database itself, then fill it like a worker would.
            var settings = new AppSettings { WorkerExe = Path.Combine(Path.GetDirectoryName(adapt)!, "worker.exe"), WorkerDir = root, DbPath = db };
            using (var p = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(adapt, $"--db \"{db}\" --summary-log \"{Path.Combine(root, "init.jsonl")}\" --force --no-require-ac")
                   { CreateNoWindow = true, UseShellExecute = false, RedirectStandardError = true })!)
            {
                p.StandardError.ReadToEnd();
                p.WaitForExit();
            }
            var rng = new Random(1);
            var start = DateTimeOffset.UtcNow.AddHours(-20);
            using (var c = new SqliteConnection($"Data Source={db};Pooling=False"))
            {
                c.Open();
                using var tx = c.BeginTransaction();
                for (var i = 0; i < 300; i++)
                {
                    var ts = start.AddMinutes(2 * i);
                    var app = new[] { "Code", "msedge", "Zed" }[rng.Next(3)];
                    using var cmd = c.CreateCommand();
                    cmd.Transaction = tx;
                    cmd.CommandText = "INSERT INTO context_records (ts, app, title, decision, features_json, created_at) VALUES (@ts, @app, @title, 'save', @f, 0)";
                    cmd.Parameters.AddWithValue("@ts", ContextDb.ToEpochUs(ts));
                    cmd.Parameters.AddWithValue("@app", app);
                    cmd.Parameters.AddWithValue("@title", $"{app} {rng.Next(1, 9)}");
                    cmd.Parameters.AddWithValue("@f", $"{{\"beh_key_presses\":{rng.Next(0, 40)},\"tmp_dwell_seconds\":{rng.Next(0, 90)},\"tmp_hour_of_day\":{ts.Hour},\"ctx_app\":\"{app}\"}}");
                    cmd.ExecuteNonQuery();
                }
                tx.Commit();
            }

            var backups = Path.Combine(root, "backups");
            var runner = new LearningRunner(settings, backups, Path.Combine(root, "runs.jsonl"));
            Assert.Null(runner.Problem());

            var result = await runner.RunOnceAsync(quick: true, labelRule: "v3");

            Assert.True(result.Ran);
            Assert.True(result.Labelled > 100, $"labelled {result.Labelled}");
            Assert.Contains(result.Decision, new[] { "keep", "deploy" });
            Assert.NotNull(result.Candidate);
            Assert.Single(new DirectoryInfo(backups).GetFiles("context-*.db"));
            Assert.True(Directory.Exists(Path.Combine(root, "models", "versions")), "the candidate must be saved under the (temp) data folder");
            Assert.Equal(result.Labelled, runner.LastRun().Labelled);  // what the dashboard shows later is what the job logged
        }
        finally
        {
            Environment.SetEnvironmentVariable(DataEnv, previous);
            SqliteConnection.ClearAllPools();
            try { Directory.Delete(root, true); } catch (IOException) { }
        }
    }
}

using ContextDashboard.Data;
using ContextDashboard.Services;
using ContextDashboard.Views;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Tests;

/// <summary>A throw-away database with the worker's schema (python-worker/storage/schema.py, v1 to v3).</summary>
public sealed class TempDb : IDisposable
{
    public string Path { get; } = System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"ctx-{Guid.NewGuid():N}.db");

    public TempDb(bool withDrcColumns = true)
    {
        Exec($@"
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, description TEXT, applied_at INTEGER NOT NULL);
            INSERT INTO schema_migrations VALUES (1, 'x', 0), ({(withDrcColumns ? 3 : 2)}, 'x', 0);
            CREATE TABLE model_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT NOT NULL UNIQUE, algorithm TEXT,
                created_at INTEGER NOT NULL, is_active INTEGER NOT NULL DEFAULT 0, metrics_json TEXT, notes TEXT);
            CREATE TABLE context_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, app TEXT, domain TEXT, title TEXT,
                decision TEXT NOT NULL, features_json TEXT NOT NULL, embedding BLOB, embedding_dim INTEGER,
                label INTEGER, labeled_at INTEGER, model_version_id INTEGER, created_at INTEGER NOT NULL,
                label_version TEXT
                {(withDrcColumns ? ", drc_would_capture INTEGER, drc_trigger TEXT" : "")});");
    }

    public void AddRecord(DateTimeOffset ts, string app, string decision, bool embedding = false, int? drc = null, int? label = null, string features = "{}")
    {
        var drcColumns = drc is null ? "" : ", drc_would_capture";
        var drcValue = drc is null ? "" : $", {drc}";
        Exec($"INSERT INTO context_records (ts, app, domain, title, decision, features_json, embedding, label, created_at{drcColumns}) "
             + $"VALUES ({ContextDb.ToEpochUs(ts)}, '{app}', 'd.com', 'T {app}', '{decision}', '{features.Replace("'", "''")}', "
             + $"{(embedding ? "x'00'" : "NULL")}, {(label is null ? "NULL" : label.ToString())}, 0{drcValue})");
    }

    public void AddModel(string version, bool active) =>
        Exec($"INSERT INTO model_versions (version, algorithm, created_at, is_active, metrics_json) "
             + $"VALUES ('{version}', 'lightgbm', {ContextDb.ToEpochUs(DateTimeOffset.UtcNow)}, {(active ? 1 : 0)}, '{{\"auc\":0.9}}')");

    private void Exec(string sql)
    {
        using var c = new SqliteConnection($"Data Source={Path};Pooling=False");
        c.Open();
        using var cmd = c.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        foreach (var f in new[] { Path, Path + "-wal", Path + "-shm" })
            if (File.Exists(f)) File.Delete(f);
    }
}

public class ContextDbTests
{
    private static readonly DateTimeOffset Now = new(2026, 10, 19, 12, 30, 0, TimeSpan.Zero);

    [Fact]
    public void Missing_database_reads_as_empty_without_creating_a_file()
    {
        var path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"nope-{Guid.NewGuid():N}.db");
        var db = new ContextDb(path);
        Assert.False(db.Exists);
        Assert.Equal(StorageSummary.Empty, db.Summary());
        Assert.Empty(db.Recent(10));
        Assert.Empty(db.Models());
        Assert.False(db.Drc().Available);
        Assert.False(File.Exists(path));
    }

    [Fact]
    public void Summary_counts_decisions_embeddings_and_labels()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddMinutes(-3), "Code", "save", embedding: true, label: 1);
        t.AddRecord(Now.AddMinutes(-2), "msedge", "discard");
        t.AddRecord(Now.AddMinutes(-1), "Code", "save");
        t.AddModel("v1", active: true);

        var s = new ContextDb(t.Path).Summary();

        Assert.Equal(3, s.Records);
        Assert.Equal(2, s.Saved);
        Assert.Equal(1, s.Discarded);
        Assert.Equal(1, s.WithEmbedding);
        Assert.Equal(1, s.Labelled);
        Assert.Equal(1, s.ModelVersions);
        Assert.Equal(3, s.SchemaVersion);
        Assert.Equal(Now.AddMinutes(-1), s.LastCapture);
        Assert.Equal(2.0 / 3, s.SaveRate, 3);
        Assert.Equal(1.0 / 3, s.EmbeddingCoverage, 3);
    }

    [Fact]
    public void Recent_is_newest_first_and_filters_by_text_and_decision()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddMinutes(-3), "Code", "save");
        t.AddRecord(Now.AddMinutes(-2), "msedge", "discard");
        t.AddRecord(Now.AddMinutes(-1), "Code", "discard");
        var db = new ContextDb(t.Path);

        Assert.Equal(["Code", "msedge", "Code"], db.Recent(10).Select(r => r.App));
        Assert.Equal(2, db.Recent(10, appContains: "code").Count);
        Assert.Single(db.Recent(10, appContains: "code", decision: "save"));
        Assert.Equal(2, db.Recent(10, decision: "discard").Count);
        Assert.Single(db.Recent(1));
    }

    [Fact]
    public void Filter_text_is_a_parameter_not_sql()
    {
        using var t = new TempDb();
        t.AddRecord(Now, "Code", "save");
        Assert.Empty(new ContextDb(t.Path).Recent(10, appContains: "'; DROP TABLE context_records; --"));
        Assert.Equal(1, new ContextDb(t.Path).Summary().Records);
    }

    [Fact]
    public void TopApps_orders_by_count_within_the_window()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddHours(-30), "Old", "save");
        t.AddRecord(Now.AddHours(-1), "Code", "save");
        t.AddRecord(Now.AddHours(-1), "Code", "discard");
        t.AddRecord(Now.AddHours(-1), "msedge", "save");

        var apps = new ContextDb(t.Path).TopApps(Now.AddHours(-24));

        Assert.Equal(["Code", "msedge"], apps.Select(a => a.App));
        Assert.Equal(2, apps[0].Records);
        Assert.Equal(1, apps[0].Saved);
    }

    [Fact]
    public void Hourly_returns_every_hour_including_empty_ones()
    {
        using var t = new TempDb();
        var db = new ContextDb(t.Path);
        t.AddRecord(Now, "Code", "save");
        t.AddRecord(Now, "Code", "discard");
        t.AddRecord(Now.AddHours(-2), "Code", "save");

        var hours = db.Hourly(6, Now);

        Assert.Equal(6, hours.Count);
        Assert.Equal(1, hours[^1].Saved);
        Assert.Equal(1, hours[^1].Discarded);
        Assert.Equal(0, hours[^2].Total);
        Assert.Equal(1, hours[^3].Total);
        Assert.Equal(5, (hours[^1].HourStart - hours[0].HourStart).TotalHours);
    }

    [Fact]
    public void Models_lists_the_active_version()
    {
        using var t = new TempDb();
        t.AddModel("old", active: false);
        t.AddModel("new", active: true);
        var models = new ContextDb(t.Path).Models();
        Assert.Equal(2, models.Count);
        Assert.Single(models, m => m.IsActive && m.Version == "new");
        Assert.Contains("auc", models[0].Metrics);
    }

    [Fact]
    public void Drc_comparison_counts_agreement()
    {
        using var t = new TempDb();
        t.AddRecord(Now.AddMinutes(1), "a", "save", drc: 1);
        t.AddRecord(Now.AddMinutes(2), "b", "save", drc: 0);
        t.AddRecord(Now.AddMinutes(3), "c", "discard", drc: 1);
        t.AddRecord(Now.AddMinutes(4), "d", "discard"); // no verdict recorded

        var d = new ContextDb(t.Path).Drc();

        Assert.True(d.Available);
        Assert.Equal(3, d.Recorded);
        Assert.Equal(2, d.DrcWouldCapture);
        Assert.Equal(2, d.ModelSaved);
        Assert.Equal(1, d.BothKeep);
        Assert.Equal(["capture", "skip", "capture", ""], new ContextDb(t.Path).Recent(10).Reverse().Select(r => r.Drc).Take(4));
    }

    [Fact]
    public void A_database_from_before_migration_3_still_reads()
    {
        using var t = new TempDb(withDrcColumns: false);
        t.AddRecord(Now, "Code", "save");
        var db = new ContextDb(t.Path);

        Assert.False(db.Drc().Available);
        var row = Assert.Single(db.Recent(10));
        Assert.Equal("", row.Drc);
        Assert.Equal(2, db.Summary().SchemaVersion);
    }

    [Fact]
    public void Reading_never_modifies_the_database()
    {
        using var t = new TempDb();
        t.AddRecord(Now, "Code", "save");
        var before = File.ReadAllBytes(t.Path);
        var db = new ContextDb(t.Path);
        db.Summary(); db.Recent(5); db.Hourly(3, Now); db.TopApps(Now.AddDays(-1)); db.Models(); db.Drc();
        Assert.Equal(before, File.ReadAllBytes(t.Path));
    }
}

public class SettingsAndFormattingTests
{
    [Fact]
    public void Defaults_are_derived_from_the_repo_layout()
    {
        var root = System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"root-{Guid.NewGuid():N}");
        Directory.CreateDirectory(System.IO.Path.Combine(root, "python-worker"));
        try
        {
            var s = new AppSettings();
            s.FillMissing(root);
            Assert.Equal(System.IO.Path.Combine(root, "python-worker", "context.db"), s.DbPath);
            Assert.EndsWith(@".venv\Scripts\python.exe", s.PythonExe);
            Assert.Equal("context-importance", s.PipeName);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void Explicit_values_are_not_overwritten()
    {
        var s = new AppSettings { DbPath = @"X:\my.db" };
        s.FillMissing(@"C:\anything");
        Assert.Equal(@"X:\my.db", s.DbPath);
    }

    [Fact]
    public void FindSystemRoot_walks_up_to_the_folder_holding_python_worker()
    {
        var root = System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"root-{Guid.NewGuid():N}");
        var deep = System.IO.Path.Combine(root, "dashboard", "ContextDashboard", "bin");
        Directory.CreateDirectory(deep);
        Directory.CreateDirectory(System.IO.Path.Combine(root, "python-worker"));
        try { Assert.Equal(root, AppSettings.FindSystemRoot(deep)); }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData(2, "just now")]
    [InlineData(30, "30 s ago")]
    [InlineData(120, "2 min ago")]
    [InlineData(7200, "2 h ago")]
    [InlineData(172800, "2 d ago")]
    public void Ago_reads_naturally(int seconds, string expected) =>
        Assert.Equal(expected, MainViewModel.Ago(TimeSpan.FromSeconds(seconds)));

    [Theory]
    [InlineData(512 * 1024, "512 KB")]
    [InlineData(5 * 1024 * 1024, "5.0 MB")]
    public void Bytes_are_formatted(long bytes, string expected) =>
        Assert.Equal(expected, MainViewModel.FormatBytes(bytes));
}

public class InstallModeTests
{
    private static string MakeInstall()
    {
        var root = Path.Combine(Path.GetTempPath(), $"inst-{Guid.NewGuid():N}");
        Directory.CreateDirectory(Path.Combine(root, "worker"));
        Directory.CreateDirectory(Path.Combine(root, "collector"));
        File.WriteAllText(Path.Combine(root, "worker", "worker.exe"), "x");
        File.WriteAllText(Path.Combine(root, "collector", "CsharpCollector.exe"), "x");
        return root;
    }

    [Fact]
    public void An_install_folder_is_recognised_by_its_worker_and_collector()
    {
        var root = MakeInstall();
        try
        {
            Assert.Equal(root, AppSettings.FindInstall(root));
            Assert.Null(AppSettings.FindInstall(Path.GetTempPath()));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void Installed_settings_point_into_the_install_and_keep_data_out_of_it()
    {
        var root = MakeInstall();
        try
        {
            var s = new AppSettings();
            s.FillMissing(root: null, installDir: root);
            Assert.Equal(Path.Combine(root, "worker", "worker.exe"), s.WorkerExe);
            Assert.Equal(Path.Combine(root, "collector", "CsharpCollector.exe"), s.CollectorExe);
            Assert.Equal(Path.Combine(AppSettings.DataDir, "context.db"), s.DbPath);
            Assert.False(s.DbPath.StartsWith(root, StringComparison.OrdinalIgnoreCase));
            Assert.Equal("", s.PythonExe);
            Assert.Null(new ServiceControl(s).Problem());
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void An_installed_dashboard_ignores_stale_program_paths_saved_by_an_older_install()
    {
        var root = MakeInstall();
        try
        {
            var s = new AppSettings
            {
                WorkerExe = @"C:\old\worker\worker.exe", WorkerDir = @"C:\old\worker", CollectorExe = @"C:\old\collector\CsharpCollector.exe",
                DbPath = @"C:\mine\context.db",
            };
            s.FillMissing(root: null, installDir: root);
            Assert.Equal(Path.Combine(root, "worker", "worker.exe"), s.WorkerExe);
            Assert.Equal(Path.Combine(root, "worker"), s.WorkerDir);
            Assert.Equal(Path.Combine(root, "collector", "CsharpCollector.exe"), s.CollectorExe);
            Assert.Equal(@"C:\mine\context.db", s.DbPath); // the user's data location is still theirs to choose
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void Worker_command_uses_the_frozen_exe_when_installed_and_python_otherwise()
    {
        var installed = new AppSettings { WorkerExe = @"C:\app\worker\worker.exe", DbPath = @"C:\d\context.db", PipeName = "p" };
        Assert.Equal($"\"{installed.WorkerExe}\" --transport namedpipe --pipe-name p --db \"{installed.DbPath}\" --no-screen-text",
            new ServiceControl(installed).WorkerCommand());

        var dev = new AppSettings { PythonExe = @"C:\py\python.exe", DbPath = @"C:\d\context.db", PipeName = "p" };
        Assert.StartsWith($"\"{dev.PythonExe}\" main.py --transport namedpipe", new ServiceControl(dev).WorkerCommand());
    }

    [Fact]
    public void Missing_worker_is_reported_not_thrown()
    {
        var s = new AppSettings { WorkerExe = @"C:\nope\worker.exe", CollectorExe = @"C:\nope\c.exe" };
        Assert.Contains("Worker not found", new ServiceControl(s).Problem());
    }
}

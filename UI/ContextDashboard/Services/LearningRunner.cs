using System.Diagnostics;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Services;

/// <summary>What one learning cycle did, read from the job's own run log (<c>adaptation_runs.jsonl</c>).</summary>
public sealed record LearningResult(
    DateTimeOffset? Finished,
    bool Ran,
    string Decision,
    int Labelled,
    string? Candidate,
    string Detail,
    string? Error)
{
    public static readonly LearningResult None = new(null, false, "none", 0, null, "No learning run yet.", null);
    public bool Deployed => Decision == "deploy";

    public string Summary =>
        Finished is null ? Detail
        : Error is not null ? $"{Finished.Value.ToLocalTime():dd MMM HH:mm}: failed. {Error}"
        : $"{Finished.Value.ToLocalTime():dd MMM HH:mm}: {Labelled:N0} records labelled, {DecisionText}. {Detail}".TrimEnd();

    private string DecisionText => Decision switch
    {
        "deploy" => "a new model was deployed",
        "keep" => "a candidate was trained but is not better, so the current model stays",
        _ => "nothing was trained",
    };
}

/// <summary>
/// Runs Model 2 (the learning job, <c>adapt.exe</c>): once on demand, or as a background daemon that waits
/// for the PC to be idle. Labels are written into the database, so a copy is taken before every manual run.
/// </summary>
public sealed class LearningRunner
{
    private const int KeepBackups = 3;
    private readonly AppSettings _settings;
    private readonly string _backupDir;
    private readonly string _runLog;

    /// <param name="backupDir">Where database copies go (default: the data folder). Tests pass a temp folder.</param>
    /// <param name="runLog">The job's JSONL run log (default: the data folder). Tests pass a temp file.</param>
    public LearningRunner(AppSettings settings, string? backupDir = null, string? runLog = null, Func<string, string, string>? help = null)
    {
        _settings = settings;
        _backupDir = backupDir ?? BackupDir;
        _runLog = runLog ?? RunLog;
        _help = help ?? HelpProbe.Run;
    }

    // ---- what the installed job understands --------------------------------------------------------------------------
    // An older adapt.exe rejects flags it does not know and the whole run fails (see HelpProbe).
    private readonly Func<string, string, string> _help;

    /// <summary>Set by <see cref="Command"/>: what was left out because the job is too old, or null.</summary>
    public string? CapabilityNote { get; private set; }

    private bool Supports(string file, string prefix, string flag) => HelpProbe.Supports(_help, file, prefix, flag);

    public static string RunLog => Path.Combine(AppSettings.DataDir, "adaptation_runs.jsonl");
    public static string BackupDir => Path.Combine(AppSettings.DataDir, "backups");

    /// <summary>Executable and arguments for the learning job (frozen exe when installed, else Python from the repo).</summary>
    internal (string File, string Args) Command(bool quick, string labelRule, bool daemon = false)
    {
        var tail = $"--db \"{_settings.DbPath}\" --summary-log \"{_runLog}\"";
        if (daemon) tail += " --loop";
        else tail += " --force --no-require-ac";
        if (quick) tail += " --label-delay-hours 1 --label-horizon-hours 1";
        if (labelRule == "kw") tail += " --label-rule kw";
        var frozen = _settings.WorkerExe.Length > 0;
        var file = frozen ? Path.Combine(Path.GetDirectoryName(_settings.WorkerExe)!, "adapt.exe") : _settings.PythonExe;
        var prefix = frozen ? "" : "-m adaptation.scheduled_job";

        CapabilityNote = null;
        if (_settings.ScreenTextActive)
        {
            tail += " --screen-memory";
            var useFilter = _settings.UseCaprlmFilter && Supports(file, prefix, "--privacy-url");
            if (useFilter) tail += $" --privacy-url {AppSettings.PrivacyUrl}";
            else if (_settings.ScreenTextKeepUnredacted) tail += " --dev-passthrough-filter";

            var model = _settings.SummariesEnabled ? _settings.ResolveLlmModel() : null;
            var summaries = model is not null && Supports(file, prefix, "--summarize");
            if (summaries) tail += $" --summarize --llm-model \"{model}\"";

            if ((_settings.UseCaprlmFilter && !useFilter) || (model is not null && !summaries))
                CapabilityNote = "The learning job is an older build and does not know the CAPRLM filter or summaries yet: "
                                 + "rebuild the worker bundle (packaging\\build_bundle.ps1) to use them.";
        }
        return (file, (prefix + " " + tail).Trim());
    }

    public string? Problem()
    {
        var (file, _) = Command(false, "v3");
        if (!File.Exists(file)) return $"Learning job not found: {file}";
        if (!File.Exists(_settings.DbPath)) return "No database yet. Start capture first, so there is something to learn from.";
        return null;
    }

    /// <summary>Backs up the database, runs one cycle and returns what the job logged. Throws if it cannot start.</summary>
    public async Task<LearningResult> RunOnceAsync(bool quick, string labelRule, CancellationToken ct = default)
    {
        if (Problem() is { } problem) throw new InvalidOperationException(problem);
        var before = LastRun();
        Backup();

        var (file, args) = Command(quick, labelRule);
        var info = new ProcessStartInfo(file, args)
        {
            WorkingDirectory = _settings.WorkerDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardError = true,
            RedirectStandardOutput = true,
        };
        using var process = Process.Start(info) ?? throw new InvalidOperationException("could not start the learning job");
        var stderr = process.StandardError.ReadToEndAsync(ct);
        var stdout = process.StandardOutput.ReadToEndAsync(ct);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromMinutes(15));
        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException)
        {
            try { process.Kill(true); } catch (InvalidOperationException) { }
            throw new TimeoutException("The learning job took longer than 15 minutes and was stopped.");
        }
        var log = await stderr + await stdout;

        var after = LastRun();
        if (process.ExitCode != 0)
            return new LearningResult(DateTimeOffset.Now, false, "none", 0, null, "", Tail(log) is { Length: > 0 } t ? t : $"exit code {process.ExitCode}");
        return after.Finished != before.Finished || after.Candidate != before.Candidate
            ? after
            : new LearningResult(DateTimeOffset.Now, false, "skipped", 0, null, ReasonFrom(log), null);
    }

    /// <summary>The newest run the job logged, or <see cref="LearningResult.None"/>.</summary>
    public LearningResult LastRun() => ParseLastRun(_runLog);

    internal static LearningResult ParseLastRun(string path)
    {
        if (!File.Exists(path)) return LearningResult.None;
        string? line;
        try
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            using var reader = new StreamReader(stream);
            line = reader.ReadToEnd().Split("\n", StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).LastOrDefault();
        }
        catch (IOException)
        {
            return LearningResult.None;
        }
        if (line is null) return LearningResult.None;
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            string? Str(string name) => root.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
            var decision = Str("decision") ?? "skipped";
            var detail = Str("skip_reason");
            if (detail is null && root.TryGetProperty("stages", out var stages))
            {
                if (decision == "skipped" && stages.TryGetProperty("retrain", out var retrain) && retrain.TryGetProperty("reason", out var r1))
                    detail = r1.GetString();
                else if (stages.TryGetProperty("validation", out var validation) && validation.TryGetProperty("reason", out var r2))
                    detail = r2.GetString();
            }
            return new LearningResult(
                DateTimeOffset.TryParse(Str("finished_at"), out var finished) ? finished : null,
                root.TryGetProperty("ran", out var ran) && ran.ValueKind == JsonValueKind.True,
                decision,
                root.TryGetProperty("contexts_labelled", out var n) && n.TryGetInt32(out var count) ? count : 0,
                Str("candidate_version"),
                detail ?? "",
                Str("error"));
        }
        catch (JsonException)
        {
            return LearningResult.None;
        }
    }

    // ---- background daemon ----------------------------------------------------------------

    /// <summary>Starts the idle-time learner (<c>adapt.exe --loop</c>) if it is not already running.</summary>
    public void StartDaemon(ServiceControl services, string labelRule = "v3")
    {
        if (services.IsRunning("learner")) return;
        if (Problem() is not null) return; // nothing to learn from yet: the next start will try again
        Directory.CreateDirectory(AppSettings.LogDir);
        var (file, args) = Command(false, labelRule, daemon: true);
        services.Launch("learner", _settings.WorkerDir, $"\"{file}\" {args}", new Dictionary<string, string>());
    }

    // ---- helpers --------------------------------------------------------------------------

    /// <summary>A consistent copy via SQLite's backup API (safe while the worker writes); keeps the newest few.</summary>
    internal void Backup()
    {
        if (!File.Exists(_settings.DbPath)) return;
        Directory.CreateDirectory(_backupDir);
        var target = Path.Combine(_backupDir, $"context-{DateTime.Now:yyyyMMdd-HHmmss}.db");
        using (var source = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = _settings.DbPath, Mode = SqliteOpenMode.ReadOnly, Pooling = false }.ToString()))
        using (var copy = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = target, Pooling = false }.ToString()))
        {
            source.Open();
            copy.Open();
            source.BackupDatabase(copy);
        }
        foreach (var old in new DirectoryInfo(_backupDir).GetFiles("context-*.db").OrderByDescending(f => f.Name).Skip(KeepBackups))
            old.Delete();
    }

    private static string Tail(string log) => string.Join(" ", log.Split("\n").Select(l => l.Trim()).Where(l => l.Length > 0).TakeLast(3));

    private static string ReasonFrom(string log)
    {
        var match = Regex.Match(log, @"decision=\w+ \((?<reason>[^)]*)\)|skipped: (?<reason>.+)");
        return match.Success ? match.Groups["reason"].Value.Trim() : Tail(log);
    }
}

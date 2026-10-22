using System.Diagnostics;

namespace ContextDashboard.Services;

public enum ServiceState { Stopped, Running }

/// <summary>
/// Starts, stops and inspects the two background processes. They are launched hidden and detached, so
/// they keep running when the dashboard window (or the tray) exits.
/// </summary>
public sealed class ServiceControl
{
    private const string CollectorProcess = "CsharpCollector";
    private readonly AppSettings _settings;
    private readonly Func<string, string, string> _help;

    /// <param name="help">Runs a program's <c>--help</c> (see <see cref="HelpProbe"/>). Tests pass a fake.</param>
    public ServiceControl(AppSettings settings, Func<string, string, string>? help = null)
    {
        _settings = settings;
        _help = help ?? HelpProbe.Run;
    }

    /// <summary>The worker is up exactly when its named pipe exists: it listens from startup onwards.</summary>
    public ServiceState Worker() =>
        File.Exists(@"\\.\pipe\" + _settings.PipeName) ? ServiceState.Running : ServiceState.Stopped;

    public ServiceState Collector()
    {
        var processes = Process.GetProcessesByName(CollectorProcess);
        foreach (var p in processes) p.Dispose();
        return processes.Length > 0 ? ServiceState.Running : ServiceState.Stopped;
    }

    /// <summary>Null when startable, otherwise what is missing.</summary>
    public string? Problem()
    {
        if (_settings.WorkerExe.Length > 0)
        {
            if (!File.Exists(_settings.WorkerExe)) return $"Worker not found: {_settings.WorkerExe}";
        }
        else
        {
            if (!File.Exists(_settings.PythonExe)) return $"Python not found: {_settings.PythonExe}";
            if (!File.Exists(Path.Combine(_settings.WorkerDir, "main.py"))) return $"main.py not found in {_settings.WorkerDir}";
        }
        if (!File.Exists(_settings.CollectorExe)) return "Collector is not built. Run 'dotnet build' in csharp-collector.";
        return null;
    }

    /// <summary>Starts the worker, waits for its pipe (loading MobileCLIP takes a while), then the collector.</summary>
    public async Task StartAsync(CancellationToken ct = default)
    {
        if (Problem() is { } problem) throw new InvalidOperationException(problem);
        Directory.CreateDirectory(AppSettings.LogDir);
        Directory.CreateDirectory(AppSettings.PidDir);

        if (Worker() == ServiceState.Stopped && !IsPidAlive("worker"))
        {
            // Without the checkpoint the worker silently stores records without embeddings.
            var env = new Dictionary<string, string>();
            if (File.Exists(_settings.CheckpointPath)) env["MOBILECLIP_S0_CHECKPOINT"] = _settings.CheckpointPath;
            var command = await Task.Run(WorkerCommand, ct); // may run the worker's --help once per build
            Launch("worker", _settings.WorkerDir, command, env);

            for (var i = 0; i < 240 && Worker() == ServiceState.Stopped; i++)
                await Task.Delay(500, ct);
        }

        if (Collector() == ServiceState.Stopped)
        {
            var dir = Path.GetDirectoryName(_settings.CollectorExe)!;
            Launch("collector", dir, CollectorCommand(), new Dictionary<string, string>());
        }

        // Model 2 in the background: it waits for the PC to be idle (and on AC power) before it does anything.
        if (_settings.BackgroundLearning)
            new LearningRunner(_settings).StartDaemon(this, _settings.LabelRule);
    }

    /// <summary>True while a process this class started (worker, collector, learner) is alive.</summary>
    public bool IsRunning(string name) => IsPidAlive(name);

    /// <summary>
    /// What the running worker said it scores with at startup (<c>rules-1.0</c> or <c>lightgbm-&lt;version&gt;</c>), from its log.
    /// Null if there is no log (for example a worker started by hand).
    /// </summary>
    public static string? WorkerScoring()
    {
        var log = Path.Combine(AppSettings.LogDir, "worker.log");
        if (!File.Exists(log)) return null;
        try
        {
            using var stream = new FileStream(log, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            stream.Seek(Math.Max(0, stream.Length - 128 * 1024), SeekOrigin.Begin);
            using var reader = new StreamReader(stream);
            string? last = null;
            string? line;
            while ((line = reader.ReadLine()) != null)
                if (line.Contains("worker scoring with ")) last = line[(line.IndexOf("scoring with ") + "scoring with ".Length)..].Trim();
            return last;
        }
        catch (IOException)
        {
            return null;
        }
    }

    /// <summary>
    /// The user's choice is passed explicitly every time, so it wins over the (read-only) config file in both directions:
    /// opting out stays out even if the file says otherwise.
    /// </summary>
    internal string CollectorCommand() =>
        $"\"{_settings.CollectorExe}\" --screen-text {(_settings.ScreenTextActive ? "on" : "off")}";

    /// <summary>The command that starts the worker: the frozen exe when installed, else Python running main.py.</summary>
    internal string WorkerCommand()
    {
        var tail = $"--transport namedpipe --pipe-name {_settings.PipeName} --db \"{_settings.DbPath}\"";
        if (!_settings.ScreenTextActive) tail += " --no-screen-text"; // defence in depth: the worker ignores any text it is sent
        else tail += $" --raw-ttl-days {_settings.ScreenTextKeepDaysEffective}";
        if (_settings.ScreenshotsActive && WorkerSupports("--screenshots"))
        {
            tail += $" --screenshots --shots-ttl-days {_settings.ScreenshotsKeepDaysEffective} --shots-max-mb {_settings.ScreenshotsMaxMbEffective}";
            if (WorkerSupports("--shots-quality")) tail += $" --shots-quality {_settings.ScreenshotsQualityEffective}";
        }
        return _settings.WorkerExe.Length > 0
            ? $"\"{_settings.WorkerExe}\" {tail}"
            : $"\"{_settings.PythonExe}\" main.py {tail}";
    }

    /// <summary>False for an older worker build that would exit on the flag; screenshots then stay off until it is rebuilt.</summary>
    internal bool WorkerSupports(string flag) => _settings.WorkerExe.Length > 0
        ? HelpProbe.Supports(_help, _settings.WorkerExe, "", flag)
        : HelpProbe.Supports(_help, _settings.PythonExe, $"\"{Path.Combine(_settings.WorkerDir, "main.py")}\"", flag);

    /// <summary>Stops the collector first (so nothing is sent to a dead pipe), then the worker.</summary>
    public void Stop()
    {
        foreach (var p in Process.GetProcessesByName(CollectorProcess))
        {
            try { p.Kill(); } catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception) { }
            p.Dispose();
        }
        if (ReadPid("learner") is { } learner) KillTree(learner);
        if (ReadPid("worker") is { } pid) KillTree(pid);
        TryDelete(PidFile("learner"));
        TryDelete(PidFile("worker"));
        TryDelete(PidFile("collector"));
    }

    /// <summary>Hidden, detached process whose output is appended to a log file. Records its PID for Stop.</summary>
    internal void Launch(string name, string workingDir, string commandLine, Dictionary<string, string> env)
    {
        var log = Path.Combine(AppSettings.LogDir, name + ".log");
        var info = new ProcessStartInfo("cmd.exe", $"/c \"{commandLine} >> \"{log}\" 2>&1\"")
        {
            WorkingDirectory = workingDir,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        foreach (var (key, value) in env) info.Environment[key] = value;
        using var process = Process.Start(info) ?? throw new InvalidOperationException($"could not start {name}");
        File.WriteAllText(PidFile(name), process.Id.ToString());
    }

    private static string PidFile(string name) => Path.Combine(AppSettings.PidDir, name + ".pid");

    private static int? ReadPid(string name) =>
        File.Exists(PidFile(name)) && int.TryParse(File.ReadAllText(PidFile(name)), out var pid) ? pid : null;

    private static bool IsPidAlive(string name)
    {
        if (ReadPid(name) is not { } pid) return false;
        try { using var p = Process.GetProcessById(pid); return !p.HasExited; }
        catch (ArgumentException) { return false; }
    }

    private static void KillTree(int pid)
    {
        using var killer = Process.Start(new ProcessStartInfo("taskkill.exe", $"/PID {pid} /T /F")
        {
            UseShellExecute = false,
            CreateNoWindow = true,
        });
        killer?.WaitForExit(5000);
    }

    private static void TryDelete(string path)
    {
        try { File.Delete(path); } catch (IOException) { }
    }
}

using System.Diagnostics;
using System.Text.Json;

namespace ContextDashboard.Services;

public sealed record WriterProgress(string Stage, int Done, int Total);

/// <summary>
/// Runs <c>memory_llm.py</c> (the sentence, session-note and day-recap stages) in the background, at below-normal priority so the PC
/// stays usable, and reports its progress. Installed, that is <c>adapt.exe memory-llm</c>; from the repo, the repo's Python. The
/// model runs on a dedicated GPU when one is usable, else on the CPU (<c>adaptation/llm_runtime.py</c>). It needs a language
/// model file; <see cref="Problem"/> says what is missing.
/// </summary>
public sealed class MemoryWriter
{
    /// <summary>Per click. About 6 minutes on a dedicated GPU (~1.5 s each); much longer on a CPU, and the run can be cancelled.</summary>
    public const int DefaultEntries = 250;
    public const int DefaultSessions = 40;

    private readonly AppSettings _settings;
    private readonly string? _startDir;
    private Process? _process;

    private readonly Func<string, string, string> _help;

    public MemoryWriter(AppSettings settings, string? startDir = null, Func<string, string, string>? help = null)
    {
        _settings = settings;
        _startDir = startDir;
        _help = help ?? HelpProbe.Run;
    }

    public bool Running { get; private set; }

    /// <summary>
    /// What runs the tool: the installed <c>adapt.exe memory-llm</c> when that build knows it (an older one would exit on it),
    /// else the repo's <c>memory_llm.py</c> with a Python. Null when neither is there.
    /// </summary>
    internal (string File, string Prefix, string Dir)? FindTool()
    {
        if (_settings.WorkerExe.Length > 0 && Path.GetDirectoryName(_settings.WorkerExe) is { } workerDir)
        {
            var adapt = Path.Combine(workerDir, "adapt.exe");
            if (File.Exists(adapt) && HelpProbe.Supports(_help, adapt, "", "memory-llm")) return (adapt, "memory-llm", workerDir);
        }
        var candidates = new List<string>();
        if (_settings.WorkerDir.Length > 0) candidates.Add(_settings.WorkerDir);
        if (AppSettings.FindSystemRoot(_startDir ?? AppContext.BaseDirectory) is { } root) candidates.Add(Path.Combine(root, "python-worker"));

        foreach (var dir in candidates.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            var script = Path.Combine(dir, "memory_llm.py");
            if (!File.Exists(script)) continue;
            var venv = Path.Combine(dir, ".venv", "Scripts", "python.exe");
            if (File.Exists(venv)) return (venv, $"\"{script}\"", dir);
            if (_settings.PythonExe.Length > 0 && File.Exists(_settings.PythonExe)) return (_settings.PythonExe, $"\"{script}\"", dir);
        }
        return null;
    }

    /// <summary>Null when it can run, otherwise what is missing.</summary>
    public string? Problem()
    {
        if (!File.Exists(_settings.MemoryPath)) return "There is no memory database yet: turn on Screen text and let a learning run build memories first.";
        if (_settings.ResolveLlmModel() is null)
            return $"No language model file found. Put a .gguf chat model in {AppSettings.LlmDir}.";
        if (FindTool() is null)
            return "Writing summaries needs a newer build of the app (adapt.exe memory-llm) or the repo's Python "
                   + "(python-worker\\.venv). Rebuild the bundle (packaging\\build_bundle.ps1).";
        return null;
    }

    internal (string File, string Args, string WorkDir)? Command(int maxEntries)
    {
        if (FindTool() is not { } tool || _settings.ResolveLlmModel() is not { } model) return null;
        return (tool.File,
                $"{tool.Prefix} --memory-db \"{_settings.MemoryPath}\" --llm-model \"{model}\" --max-entries {maxEntries} --max-sessions {DefaultSessions}",
                tool.Dir);
    }

    /// <summary>One <c>PROGRESS {json}</c> line from the tool, or null for any other line.</summary>
    internal static WriterProgress? ParseProgress(string line)
    {
        const string prefix = "PROGRESS ";
        if (!line.StartsWith(prefix, StringComparison.Ordinal)) return null;
        try
        {
            using var doc = JsonDocument.Parse(line[prefix.Length..]);
            var r = doc.RootElement;
            return new WriterProgress(r.GetProperty("stage").GetString() ?? "", r.GetProperty("done").GetInt32(), r.GetProperty("total").GetInt32());
        }
        catch (Exception ex) when (ex is JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        {
            return null;
        }
    }

    /// <summary>Runs until the tool exits. <paramref name="onProgress"/> is called from a background thread.</summary>
    public async Task<string> RunAsync(int maxEntries, Action<WriterProgress> onProgress, CancellationToken ct = default)
    {
        if (Running) return "Already running.";
        if (Problem() is { } problem) return problem;
        var (file, args, dir) = Command(maxEntries)!.Value;

        Running = true;
        try
        {
            var info = new ProcessStartInfo(file, args)
            {
                WorkingDirectory = dir, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
            };
            _process = Process.Start(info) ?? throw new InvalidOperationException("could not start the tool");
            try { _process.PriorityClass = ProcessPriorityClass.BelowNormal; } catch (InvalidOperationException) { }

            var errors = _process.StandardError.ReadToEndAsync(ct);
            var summary = "";
            while (await _process.StandardOutput.ReadLineAsync(ct) is { } line)
            {
                if (ParseProgress(line) is { } progress) onProgress(progress);
                else if (line.StartsWith("summaries:", StringComparison.Ordinal)) summary = line;
            }
            await _process.WaitForExitAsync(ct);
            if (_process.ExitCode != 0) return $"The tool stopped with an error (code {_process.ExitCode}). {Tail(await errors)}";
            return DescribeSummary(summary);
        }
        catch (OperationCanceledException)
        {
            Kill();
            return "Cancelled. Anything already written is kept.";
        }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception or IOException)
        {
            return "Could not run the tool: " + ex.Message;
        }
        finally
        {
            Running = false;
            _process?.Dispose();
            _process = null;
        }
    }

    public void Cancel() => Kill();

    private void Kill()
    {
        try { if (_process is { HasExited: false }) _process.Kill(entireProcessTree: true); }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception) { }
    }

    private static string Tail(string text)
    {
        var last = text.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).LastOrDefault();
        return last is null ? "" : last.Length > 160 ? last[..160] : last;
    }

    /// <summary>"summaries: {...json...}" -> a plain sentence for the status line.</summary>
    internal static string DescribeSummary(string line)
    {
        try
        {
            using var doc = JsonDocument.Parse(line["summaries:".Length..]);
            var r = doc.RootElement;
            var waiting = r.GetProperty("waiting").GetInt32();
            var done = r.GetProperty("summarized").GetInt32();
            if (waiting == 0) return "Nothing was waiting for a sentence. Session notes and day recaps were refreshed.";
            return $"Wrote {done} of {waiting} sentences, then refreshed session notes and day recaps.";
        }
        catch (Exception ex) when (ex is JsonException or KeyNotFoundException or ArgumentOutOfRangeException)
        {
            return "Finished.";
        }
    }
}

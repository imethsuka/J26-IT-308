using System.Diagnostics;
using System.Text.Json;

namespace ContextDashboard.Services;

public sealed record ChampionResult(bool Ok, bool Changed, string Message);

/// <summary>
/// The Make champion button: runs <c>adapt.exe champion --db ... set &lt;version&gt;</c> (python-worker/adaptation/champion.py),
/// or the repo's Python. It checks the saved model loads before switching and records the change; nothing is deleted, so
/// the previous champion can be chosen again the same way. The worker picks the new champion up when capture restarts.
/// </summary>
public sealed class ChampionSwitch
{
    private readonly AppSettings _settings;
    private readonly Func<string, string, string> _help;

    public ChampionSwitch(AppSettings settings, Func<string, string, string>? help = null)
    {
        _settings = settings;
        _help = help ?? HelpProbe.Run;
    }

    /// <summary>The installed <c>adapt.exe champion</c> when that build knows it, else the repo's Python; null when neither.</summary>
    internal (string File, string Prefix, string Dir)? FindTool()
    {
        if (_settings.WorkerExe.Length > 0 && Path.GetDirectoryName(_settings.WorkerExe) is { } workerDir)
        {
            var adapt = Path.Combine(workerDir, "adapt.exe");
            if (File.Exists(adapt) && HelpProbe.Supports(_help, adapt, "", "adapt.exe champion")) return (adapt, "champion", workerDir);
        }
        if (_settings.WorkerDir.Length > 0 && File.Exists(Path.Combine(_settings.WorkerDir, "adaptation", "champion.py"))
            && _settings.PythonExe.Length > 0 && File.Exists(_settings.PythonExe))
            return (_settings.PythonExe, "-m adaptation.champion", _settings.WorkerDir);
        return null;
    }

    internal (string File, string Args, string Dir)? Command(string version, string reason)
    {
        if (FindTool() is not { } tool) return null;
        return (tool.File, $"{tool.Prefix} --db \"{_settings.DbPath}\" set \"{version}\" --reason \"{reason.Replace("\"", "'")}\"", tool.Dir);
    }

    public async Task<ChampionResult> SetAsync(string version, string reason, CancellationToken ct = default)
    {
        if (Command(version, reason) is not { } command)
            return new(false, false, "Choosing a champion needs a newer build of the app (adapt.exe champion). Rebuild the bundle (packaging\\build_bundle.ps1).");
        var info = new ProcessStartInfo(command.File, command.Args)
        {
            WorkingDirectory = command.Dir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        using var process = Process.Start(info) ?? throw new InvalidOperationException("could not start adapt.exe");
        var stdout = process.StandardOutput.ReadToEndAsync(ct);
        var stderr = process.StandardError.ReadToEndAsync(ct);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromMinutes(2));
        await process.WaitForExitAsync(timeout.Token);
        return Parse(await stdout, await stderr, process.ExitCode);
    }

    /// <summary>The command prints one JSON line: <c>{"champion": ..., "changed": ..., "previous": ...}</c> or <c>{"error": ...}</c>.</summary>
    internal static ChampionResult Parse(string stdout, string stderr, int exitCode)
    {
        var line = stdout.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).LastOrDefault(l => l.StartsWith('{'));
        if (line is null)
            return new(false, false, $"The champion command failed (exit {exitCode}). {stderr.Trim().Split('\n').LastOrDefault()}".Trim());
        try
        {
            using var json = JsonDocument.Parse(line);
            var root = json.RootElement;
            if (root.TryGetProperty("error", out var error)) return new(false, false, error.GetString() ?? "failed");
            var champion = root.GetProperty("champion").GetString();
            var changed = root.TryGetProperty("changed", out var c) && c.GetBoolean();
            if (!changed) return new(true, false, $"{champion} is already the champion.");
            var previous = root.TryGetProperty("previous", out var p) && p.ValueKind == JsonValueKind.String ? p.GetString() : "the rules";
            return new(true, true, $"{champion} is now the champion (was {previous}). {previous} keeps scoring in shadow mode.");
        }
        catch (JsonException)
        {
            return new(false, false, $"Unexpected output from the champion command: {line}");
        }
    }
}

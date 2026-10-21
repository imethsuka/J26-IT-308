using System.Diagnostics;
using System.IO;
using System.Net.Http;
using Suite.Core;

namespace CAPRLMComparison.Services;

/// <summary>
/// The local CAPRLM service (FastAPI on <see cref="ApiClient.BaseUrl"/>). Installed app: the frozen
/// <c>caprlm\caprlm-service.exe</c> next to the dashboard. From the repo: <c>python -m uvicorn</c> in the
/// <c>CAPRLM</c> folder. Either way it is launched hidden, and stopped on exit only if this app started it.
/// </summary>
public sealed class CaprlmBackend : IBackend
{
    private const int Port = 8787;
    private static readonly TimeSpan StartTimeout = TimeSpan.FromSeconds(120);   // spaCy + model load

    private readonly HttpClient _http = new() { BaseAddress = new Uri(ApiClient.BaseUrl), Timeout = TimeSpan.FromSeconds(2) };
    private readonly SemaphoreSlim _gate = new(1, 1);
    private Process? _started;

    public string Name => "Privacy service";
    public bool StartWithApp => true;

    public async Task<bool> IsRunningAsync()
    {
        try
        {
            using var resp = await _http.GetAsync("/health");
            return resp.IsSuccessStatusCode;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException) { return false; }
    }

    public async Task<string?> EnsureStartedAsync()
    {
        await _gate.WaitAsync();
        try
        {
            if (await IsRunningAsync()) return null;
            if (_started is { HasExited: false }) return await WaitHealthyAsync();

            var (info, problem) = BuildStartInfo();
            if (info is null) return problem;
            try { _started = Process.Start(info); }
            catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                return "Could not start the privacy service: " + ex.Message;
            }
            return await WaitHealthyAsync();
        }
        finally { _gate.Release(); }
    }

    public Task ShutdownAsync()
    {
        try
        {
            if (_started is { HasExited: false }) _started.Kill(entireProcessTree: true);
        }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception) { }
        _started?.Dispose();
        _started = null;
        return Task.CompletedTask;
    }

    private async Task<string?> WaitHealthyAsync()
    {
        var until = DateTime.UtcNow + StartTimeout;
        while (DateTime.UtcNow < until)
        {
            if (await IsRunningAsync()) return null;
            if (_started is { HasExited: true })
                return $"The privacy service exited while starting (code {_started.ExitCode}). See {LogPath}.";
            await Task.Delay(1000);
        }
        return $"The privacy service did not become ready within {StartTimeout.TotalSeconds:0} s. See {LogPath}.";
    }

    // ---- locating it -------------------------------------------------------------------------------

    private static string LogPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "context-importance", "logs", "caprlm.log");

    private static (ProcessStartInfo? Info, string? Problem) BuildStartInfo()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);

        var frozen = Path.Combine(AppContext.BaseDirectory, "caprlm", "caprlm-service.exe");
        string command, workDir;
        if (File.Exists(frozen))
        {
            command = $"\"{frozen}\" --port {Port}";
            workDir = Path.GetDirectoryName(frozen)!;
        }
        else if (FindRepoDir() is { } repo)
        {
            var venv = Path.Combine(repo, ".venv", "Scripts", "python.exe");
            var python = File.Exists(venv) ? $"\"{venv}\"" : "python";
            command = $"{python} -m uvicorn research.api.service:app --port {Port}";
            workDir = repo;
        }
        else
        {
            return (null, "CAPRLM was not found: no caprlm\\caprlm-service.exe next to the app and no CAPRLM folder above it.");
        }

        // cmd lets one redirect capture both streams in a log without the UI process reading pipes.
        return (new ProcessStartInfo("cmd.exe", $"/c \"{command} >> \"{LogPath}\" 2>&1\"")
        {
            WorkingDirectory = workDir,
            UseShellExecute = false,
            CreateNoWindow = true,
        }, null);
    }

    /// <summary>The <c>CAPRLM</c> folder (the one holding <c>research\api\service.py</c>) above or beside the exe.</summary>
    internal static string? FindRepoDir()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "CAPRLM");
            if (File.Exists(Path.Combine(candidate, "research", "api", "service.py"))) return candidate;
        }
        return null;
    }
}

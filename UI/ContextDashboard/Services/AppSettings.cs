using System.Text.Json;

namespace ContextDashboard.Services;

/// <summary>
/// Where things are. Saved to <c>%LOCALAPPDATA%\context-importance\dashboard.json</c>, so nothing
/// personal is written into the repository. Missing values are auto-detected from the repo layout.
/// </summary>
public sealed class AppSettings
{
    public string DbPath { get; set; } = "";
    public string WorkerDir { get; set; } = "";
    public string PythonExe { get; set; } = "";
    /// <summary>Installed app: the frozen worker (<c>worker\worker.exe</c>). Empty when running from the repo.</summary>
    public string WorkerExe { get; set; } = "";
    public string CollectorExe { get; set; } = "";
    public string CheckpointPath { get; set; } = "";
    public string PipeName { get; set; } = "context-importance";
    /// <summary>Run Model 2's learning job in the background (it waits for an idle PC) whenever capture is started.</summary>
    public bool BackgroundLearning { get; set; } = true;
    /// <summary>Start capture (collector + worker) when the app opens. Off by default: capture counts input and takes screenshots, so it stays an explicit choice.</summary>
    public bool AutoStartCapture { get; set; }
    /// <summary>Label rule for the learning job: "v3" (came back within the horizon) or "kw" (Keep-Worthiness).</summary>
    public string LabelRule { get; set; } = "v3";

    // ---- Screen text (opt-in, off by default) -----------------------------------------------------
    /// <summary>Bumped whenever <see cref="ConsentText"/> changes, so everyone is asked again.</summary>
    public const int CurrentConsentVersion = 1;

    public const string ConsentText =
        "Screen text capture reads the words visible in the window you are using. It never reads passwords, the box you "
        + "are typing in, or apps and sites on your exclusion list.\n\n"
        + "The words are kept encrypted on this PC for up to 3 days, and a background job turns them into memory entries "
        + "while the PC is idle. Until a privacy filter is installed, those entries contain only the app, site, page title "
        + "and counts, and no text is kept beyond the 3 days. Nothing leaves this PC.\n\n"
        + "You can switch this off and delete everything it captured at any time on the Screen text tab.\n\n"
        + "If you take part in a study, your consent form must cover extracted text before you turn this on.";

    /// <summary>The user's choice. Off unless they opted in on the Screen text tab.</summary>
    public bool ScreenTextEnabled { get; set; }
    /// <summary>Which consent text the user agreed to (0 = never). A different version means they must agree again.</summary>
    public int ScreenTextConsentVersion { get; set; }
    public string ScreenTextConsentAt { get; set; } = "";
    /// <summary>Developer only: keep text with no privacy filter (nothing is redacted). Off unless asked for.</summary>
    public bool ScreenTextKeepUnredacted { get; set; }

    /// <summary>Screen text is on only with both the switch and a consent that matches the current text.</summary>
    public bool ScreenTextActive => ScreenTextEnabled && ScreenTextConsentVersion == CurrentConsentVersion;

    public void OptInScreenText(DateTimeOffset now)
    {
        ScreenTextEnabled = true;
        ScreenTextConsentVersion = CurrentConsentVersion;
        ScreenTextConsentAt = now.ToString("O");
    }

    /// <summary>Switching off also withdraws the consent and the developer option.</summary>
    public void OptOutScreenText()
    {
        ScreenTextEnabled = false;
        ScreenTextKeepUnredacted = false;
        ScreenTextConsentVersion = 0;
        ScreenTextConsentAt = "";
    }

    /// <summary><c>raw_buffer.db</c> and <c>memory.db</c> sit beside the context database, as the worker and the job expect.</summary>
    public string RawBufferPath => Path.Combine(Path.GetDirectoryName(Path.GetFullPath(DbPath)) ?? ".", "raw_buffer.db");
    public string MemoryPath => Path.Combine(Path.GetDirectoryName(Path.GetFullPath(DbPath)) ?? ".", "memory.db");

    public static string DataDir { get; } =
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "context-importance");

    public static string SettingsFile => Path.Combine(DataDir, "dashboard.json");
    public static string LogDir => Path.Combine(DataDir, "logs");
    public static string PidDir => Path.Combine(DataDir, "run");

    public static AppSettings Load(string? startDir = null)
    {
        var settings = new AppSettings();
        try
        {
            if (File.Exists(SettingsFile))
                settings = JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(SettingsFile)) ?? settings;
        }
        catch (Exception ex) when (ex is IOException or JsonException)
        {
            // A damaged settings file falls back to detection rather than stopping the app.
        }
        var start = startDir ?? AppContext.BaseDirectory;
        settings.FillMissing(FindSystemRoot(start), FindInstall(start));
        return settings;
    }

    public void Save()
    {
        Directory.CreateDirectory(DataDir);
        File.WriteAllText(SettingsFile, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
    }

    /// <summary>
    /// The <c>context-importance-system</c> folder: either the nearest ancestor that directly contains
    /// <c>python-worker</c> (the dashboard running from inside that tree, e.g. during development before the
    /// repo-root move), or the nearest ancestor that has a <c>context-importance-system\python-worker</c> child
    /// (the dashboard running from the repo root, as a sibling of <c>context-importance-system</c>, which is
    /// where it normally lives now). Checked at every level on the way up so either layout resolves.
    /// </summary>
    public static string? FindSystemRoot(string startDir)
    {
        for (var dir = new DirectoryInfo(startDir); dir != null; dir = dir.Parent)
        {
            if (Directory.Exists(Path.Combine(dir.FullName, "python-worker")))
                return dir.FullName;

            var sibling = Path.Combine(dir.FullName, "context-importance-system");
            if (Directory.Exists(Path.Combine(sibling, "python-worker")))
                return sibling;
        }
        return null;
    }

    /// <summary>The install folder: the one holding <c>worker\worker.exe</c> and <c>collector\CsharpCollector.exe</c>.</summary>
    public static string? FindInstall(string startDir)
    {
        var dir = new DirectoryInfo(startDir);
        return File.Exists(Path.Combine(dir.FullName, "worker", "worker.exe"))
               && File.Exists(Path.Combine(dir.FullName, "collector", "CsharpCollector.exe"))
            ? dir.FullName
            : null;
    }

    /// <summary>
    /// Installed app: everything comes from the install folder (always, even over saved values) and user data goes to
    /// <see cref="DataDir"/>, never next to the program files. From the repo: the repo layout. Saved values are kept there.
    /// </summary>
    internal void FillMissing(string? root, string? installDir = null)
    {
        if (installDir != null)
        {
            // The programs are always the ones installed next to this exe, never a path saved earlier: a stale saved path
            // pointed a newer dashboard at an older worker, which rejected the flags the dashboard passes it.
            WorkerExe = Path.Combine(installDir, "worker", "worker.exe");
            WorkerDir = Path.Combine(installDir, "worker");
            CollectorExe = Path.Combine(installDir, "collector", "CsharpCollector.exe");
            if (string.IsNullOrEmpty(DbPath)) DbPath = Path.Combine(DataDir, "context.db");
            return;
        }
        if (root == null) return;
        if (string.IsNullOrEmpty(WorkerDir)) WorkerDir = Path.Combine(root, "python-worker");
        if (string.IsNullOrEmpty(DbPath)) DbPath = Path.Combine(WorkerDir, "context.db");
        if (string.IsNullOrEmpty(PythonExe)) PythonExe = Path.Combine(WorkerDir, ".venv", "Scripts", "python.exe");
        if (string.IsNullOrEmpty(CollectorExe)) CollectorExe = FindCollectorExe(root) ?? "";
        if (string.IsNullOrEmpty(CheckpointPath))
        {
            // Member1 sits next to context-importance-system in the git root.
            var candidate = Path.GetFullPath(Path.Combine(root, "..", "Member1", "checkpoints", "mobileclip_s0.pt"));
            if (File.Exists(candidate)) CheckpointPath = candidate;
        }
    }

    private static string? FindCollectorExe(string root)
    {
        var bin = Path.Combine(root, "csharp-collector", "src", "CsharpCollector", "bin");
        if (!Directory.Exists(bin)) return null;
        return Directory.EnumerateFiles(bin, "CsharpCollector.exe", SearchOption.AllDirectories)
            .OrderByDescending(File.GetLastWriteTimeUtc).FirstOrDefault();
    }
}

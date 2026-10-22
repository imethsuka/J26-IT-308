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

    // ---- Memories: privacy filter and local summaries (both only matter once screen text is on) --------------------------
    /// <summary>Where CAPRLM's local service listens. The learning job only accepts a loopback address.</summary>
    public const string PrivacyUrl = "http://127.0.0.1:8787";
    /// <summary>Put page text through CAPRLM before it is kept. Replaces the unredacted developer option when both are set.</summary>
    public bool UseCaprlmFilter { get; set; } = true;
    /// <summary>Let a small local language model write a sentence for each important memory.</summary>
    public bool SummariesEnabled { get; set; } = true;
    /// <summary>One GGUF model file. Empty = the <c>%LOCALAPPDATA%\context-importance\models\llm</c> folder, from which the
    /// worker gives a dedicated GPU the largest model it can take and the CPU the smallest (adaptation/llm_runtime.py).</summary>
    public string LlmModelPath { get; set; } = "";

    /// <summary>Tests point this at a temp folder, so the answer does not depend on what is installed on the PC.</summary>
    internal string? LlmSearchDir { get; set; }

    public static string LlmDir => Path.Combine(DataDir, "models", "llm");
    /// <summary>Models kept but not used automatically (older or compared ones). They can still be chosen in Settings.</summary>
    public static string LlmCandidatesDir => Path.Combine(DataDir, "models", "llm-candidates");

    /// <summary>Every model the user can choose: the automatic folder's and the kept candidates, smallest first.</summary>
    public IReadOnlyList<string> AvailableLlmModels() =>
        new[] { LlmSearchDir ?? LlmDir, LlmCandidatesDir }.Where(Directory.Exists)
            .SelectMany(d => Directory.EnumerateFiles(d, "*.gguf")).OrderBy(f => new FileInfo(f).Length).ToList();

    /// <summary>What to pass as <c>--llm-model</c>: the chosen file (when it still exists), else the models folder when it holds a <c>.gguf</c>, else null
    /// (then no summaries are written).</summary>
    public string? ResolveLlmModel()
    {
        // A chosen model that has since gone falls back to automatic rather than turning summaries off.
        if (LlmModelPath.Length > 0 && File.Exists(LlmModelPath)) return LlmModelPath;
        var dir = LlmSearchDir ?? LlmDir;
        return Directory.Exists(dir) && Directory.EnumerateFiles(dir, "*.gguf").Any() ? dir : null;
    }
    /// <summary>Label rule for the learning job: "v3" (came back within the horizon) or "kw" (Keep-Worthiness).</summary>
    public string LabelRule { get; set; } = "v3";

    // ---- Screen text (opt-in, off by default) -----------------------------------------------------
    /// <summary>Bumped whenever <see cref="ConsentText"/> changes, so everyone is asked again. 2: the keep time became a setting (7 days by default).</summary>
    public const int CurrentConsentVersion = 2;

    public const string ConsentText =
        "Screen text capture reads the words visible in the window you are using. It never reads passwords, the box you "
        + "are typing in, or apps and sites on your exclusion list.\n\n"
        + "The words are kept encrypted on this PC for 7 days, or for the time you choose in Settings (1 to 90 days), and a "
        + "background job turns them into memory entries while the PC is idle. Until a privacy filter is installed, those "
        + "entries contain only the app, site, page title and counts, and no text is kept beyond that time. Nothing leaves "
        + "this PC.\n\n"
        + "You can switch this off and delete everything it captured at any time on the Screen text tab.\n\n"
        + "If you take part in a study, your consent form must cover extracted text before you turn this on.";

    /// <summary>The user's choice. Off unless they opted in on the Screen text tab.</summary>
    public bool ScreenTextEnabled { get; set; }

    /// <summary>Days captured screen text is kept before the worker deletes it (Settings). Clamped to 1 to 90 when used.</summary>
    public int ScreenTextKeepDays { get; set; } = DefaultScreenTextKeepDays;
    public const int DefaultScreenTextKeepDays = 7, MaxScreenTextKeepDays = 90;
    public int ScreenTextKeepDaysEffective => Math.Clamp(ScreenTextKeepDays, 1, MaxScreenTextKeepDays);
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

    // ---- Screenshots for the history slider (opt-in, off by default, a consent of its own) --------------------------
    /// <summary>2: the keep time and space became settings (30 days and 5 GB by default).</summary>
    public const int CurrentScreenshotsConsentVersion = 2;

    /// <summary>Days a screenshot is kept, and the most disk space they may take (Settings). The worker deletes past either
    /// limit, oldest first. Clamped when used: 1 to 365 days, 0.5 to 200 GB.</summary>
    public int ScreenshotsKeepDays { get; set; } = DefaultScreenshotsKeepDays;
    public int ScreenshotsMaxMb { get; set; } = DefaultScreenshotsMaxMb;
    public const int DefaultScreenshotsKeepDays = 30, MaxScreenshotsKeepDays = 365;
    public const int DefaultScreenshotsMaxMb = 5000, MinScreenshotsMaxMb = 500, MaxScreenshotsMaxMb = 200_000;
    public int ScreenshotsKeepDaysEffective => Math.Clamp(ScreenshotsKeepDays, 1, MaxScreenshotsKeepDays);
    /// <summary>JPEG quality of kept screenshots, percent (Settings). Lower is smaller and blurrier. Clamped to 10 to 95.</summary>
    public int ScreenshotsQuality { get; set; } = DefaultScreenshotsQuality;
    public const int DefaultScreenshotsQuality = 50;
    public int ScreenshotsQualityEffective => Math.Clamp(ScreenshotsQuality, 10, 95);
    public int ScreenshotsMaxMbEffective => Math.Clamp(ScreenshotsMaxMb, MinScreenshotsMaxMb, MaxScreenshotsMaxMb);

    public const string ScreenshotsConsentText =
        "Screenshots keep a picture of your screen each time the importance model decides a moment is worth saving, so "
        + "you can scroll back through your day on the History page.\n\n"
        + "A screenshot shows the whole main monitor, including windows behind the one you are using. The privacy filter "
        + "works on text only, so nothing in a screenshot is hidden or blurred. Apps and sites on your exclusion list are "
        + "never captured while they are in front.\n\n"
        + "Screenshots are encrypted for your Windows account and stay on this PC. They are deleted after 30 days, or "
        + "sooner when they pass 5 GB (oldest first), unless you choose other limits in Settings. You can switch this off "
        + "and delete them all at any time.\n\n"
        + "If you take part in a study, your consent form must cover screenshots before you turn this on.";

    public bool ScreenshotsEnabled { get; set; }
    public int ScreenshotsConsentVersion { get; set; }
    public string ScreenshotsConsentAt { get; set; } = "";

    /// <summary>On only with both the switch and a consent that matches the current text.</summary>
    public bool ScreenshotsActive => ScreenshotsEnabled && ScreenshotsConsentVersion == CurrentScreenshotsConsentVersion;

    public void OptInScreenshots(DateTimeOffset now)
    {
        ScreenshotsEnabled = true;
        ScreenshotsConsentVersion = CurrentScreenshotsConsentVersion;
        ScreenshotsConsentAt = now.ToString("O");
    }

    public void OptOutScreenshots()
    {
        ScreenshotsEnabled = false;
        ScreenshotsConsentVersion = 0;
        ScreenshotsConsentAt = "";
    }

    /// <summary><c>raw_buffer.db</c>, <c>memory.db</c> and <c>screenshots.db</c> sit beside the context database, as the worker and the job expect.</summary>
    public string RawBufferPath => Path.Combine(Path.GetDirectoryName(Path.GetFullPath(DbPath)) ?? ".", "raw_buffer.db");
    public string MemoryPath => Path.Combine(Path.GetDirectoryName(Path.GetFullPath(DbPath)) ?? ".", "memory.db");
    public string ScreenshotsPath => Path.Combine(Path.GetDirectoryName(Path.GetFullPath(DbPath)) ?? ".", "screenshots.db");

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
    /// The system folder (<c>PCCILM</c>, called <c>context-importance-system</c> before the rename): either the nearest
    /// ancestor that directly contains <c>python-worker</c> (the dashboard running from inside that tree), or the nearest
    /// ancestor that has a <c>PCCILM\python-worker</c> (or old-name) child: the dashboard running from the repo root or
    /// from <c>Builds\ContextImportance</c>. Checked at every level on the way up so every layout resolves.
    /// </summary>
    private static readonly string[] SystemFolderNames = ["PCCILM", "context-importance-system"];

    public static string? FindSystemRoot(string startDir)
    {
        for (var dir = new DirectoryInfo(startDir); dir != null; dir = dir.Parent)
        {
            if (Directory.Exists(Path.Combine(dir.FullName, "python-worker")))
                return dir.FullName;

            foreach (var name in SystemFolderNames)
            {
                var sibling = Path.Combine(dir.FullName, name);
                if (Directory.Exists(Path.Combine(sibling, "python-worker")))
                    return sibling;
            }
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

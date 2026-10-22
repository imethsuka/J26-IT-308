using ContextDashboard.Services;

namespace ContextDashboard.Tests;

/// <summary>
/// The learning job gets the CAPRLM filter and the summary options only when it understands them, so an older adapt.exe
/// is never handed a flag it rejects (that failed the whole run once).
/// </summary>
public class MemoryJobFlagsTests : IDisposable
{
    private const string NewHelp = "usage: ... [--screen-memory] [--privacy-url PRIVACY_URL] [--summarize] [--llm-model LLM_MODEL]";
    private const string OldHelp = "usage: ... [--screen-memory] [--dev-passthrough-filter]";
    private readonly string _dir = Path.Combine(Path.GetTempPath(), $"flags-{Guid.NewGuid():N}");

    public MemoryJobFlagsTests() => Directory.CreateDirectory(_dir);
    public void Dispose() { try { Directory.Delete(_dir, true); } catch (IOException) { } }

    private AppSettings Settings(bool screenText = true, bool keepUnredacted = false, bool model = true)
    {
        var s = new AppSettings { WorkerExe = Path.Combine(_dir, "worker", "worker.exe"), DbPath = Path.Combine(_dir, "context.db") };
        if (screenText) s.OptInScreenText(DateTimeOffset.Now);
        s.ScreenTextKeepUnredacted = keepUnredacted && screenText;
        if (model)
        {
            var gguf = Path.Combine(_dir, "m.gguf");
            File.WriteAllText(gguf, "x");
            s.LlmModelPath = gguf;
        }
        return s;
    }

    // a different "file" per test so the per-file help cache never leaks between tests
    private LearningRunner Runner(AppSettings s, string help) => new(s, help: (file, prefix) => help);

    private (string File, string Args) Cmd(AppSettings s, string help)
    {
        Directory.CreateDirectory(Path.Combine(_dir, "worker"));
        File.WriteAllText(Path.Combine(_dir, "worker", "adapt.exe"), Guid.NewGuid().ToString());   // new timestamp/key per test
        return Runner(s, help).Command(false, "v3");
    }

    [Fact]
    public void A_new_job_gets_the_filter_and_the_model()
    {
        var s = Settings();
        var (_, args) = Cmd(s, NewHelp);

        Assert.Contains("--screen-memory", args);
        Assert.Contains($"--privacy-url {AppSettings.PrivacyUrl}", args);
        Assert.Contains($"--summarize --llm-model \"{s.LlmModelPath}\"", args);
        Assert.DoesNotContain("--dev-passthrough-filter", args);
    }

    [Fact]
    public void The_filter_replaces_the_unredacted_developer_option()
    {
        var (_, args) = Cmd(Settings(keepUnredacted: true), NewHelp);

        Assert.Contains("--privacy-url", args);
        Assert.DoesNotContain("--dev-passthrough-filter", args);
    }

    [Fact]
    public void An_old_job_is_not_given_flags_it_would_reject_and_the_user_is_told()
    {
        var s = Settings(keepUnredacted: true);
        Directory.CreateDirectory(Path.Combine(_dir, "worker"));
        File.WriteAllText(Path.Combine(_dir, "worker", "adapt.exe"), Guid.NewGuid().ToString());
        var runner = Runner(s, OldHelp);

        var (_, args) = runner.Command(false, "v3");

        Assert.DoesNotContain("--privacy-url", args);
        Assert.DoesNotContain("--summarize", args);
        Assert.Contains("--dev-passthrough-filter", args);        // what it did before
        Assert.Contains("older build", runner.CapabilityNote);
    }

    [Fact]
    public void Nothing_is_added_when_screen_text_is_off()
    {
        var (_, args) = Cmd(Settings(screenText: false), NewHelp);

        Assert.DoesNotContain("--screen-memory", args);
        Assert.DoesNotContain("--privacy-url", args);
        Assert.DoesNotContain("--summarize", args);
    }

    [Fact]
    public void No_model_file_means_no_summaries_and_no_complaint()
    {
        var s = Settings(model: false);
        s.LlmModelPath = "";
        s.LlmSearchDir = Path.Combine(_dir, "no-models");
        var runner = Runner(s, NewHelp);
        Directory.CreateDirectory(Path.Combine(_dir, "worker"));
        File.WriteAllText(Path.Combine(_dir, "worker", "adapt.exe"), Guid.NewGuid().ToString());

        var (_, args) = runner.Command(false, "v3");

        Assert.DoesNotContain("--summarize", args);
        Assert.DoesNotContain("--llm-model", args);
        Assert.Null(runner.CapabilityNote);
    }

    [Fact]
    public void The_user_can_switch_either_part_off()
    {
        var s = Settings();
        s.UseCaprlmFilter = false;
        s.SummariesEnabled = false;
        var (_, args) = Cmd(s, NewHelp);

        Assert.DoesNotContain("--privacy-url", args);
        Assert.DoesNotContain("--summarize", args);
    }

    [Fact]
    public void The_model_is_found_in_the_models_folder_when_no_path_is_set()
    {
        var models = Path.Combine(_dir, "models");
        Directory.CreateDirectory(models);
        File.WriteAllText(Path.Combine(models, "b.gguf"), "x");
        File.WriteAllText(Path.Combine(models, "a.gguf"), "x");
        File.WriteAllText(Path.Combine(models, "notes.txt"), "x");
        var s = new AppSettings { LlmSearchDir = models };

        Assert.Equal(models, s.ResolveLlmModel());                             // the folder: the worker picks GPU or CPU model
        File.Delete(Path.Combine(models, "a.gguf"));
        File.Delete(Path.Combine(models, "b.gguf"));
        Assert.Null(s.ResolveLlmModel());                                      // only .gguf files count
        s.LlmModelPath = Path.Combine(_dir, "missing.gguf");
        File.WriteAllText(Path.Combine(models, "c.gguf"), "x");
        Assert.Equal(models, s.ResolveLlmModel());                             // a chosen model that is gone falls back to automatic
        s.LlmModelPath = Path.Combine(models, "c.gguf");
        Assert.Equal(s.LlmModelPath, s.ResolveLlmModel());                     // a chosen model that exists wins
    }
}

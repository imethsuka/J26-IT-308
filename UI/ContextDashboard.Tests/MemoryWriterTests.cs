using ContextDashboard.Services;

namespace ContextDashboard.Tests;

/// <summary>The "Write summaries now" button: finding the tool, saying what is missing, and reading its progress lines.</summary>
public class MemoryWriterTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), $"writer-{Guid.NewGuid():N}");

    public MemoryWriterTests() => Directory.CreateDirectory(_dir);
    public void Dispose() { try { Directory.Delete(_dir, true); } catch (IOException) { } }

    private static void Touch(string root, params string[] parts)
    {
        var path = Path.Combine([root, .. parts]);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, "x");
    }

    /// <summary>A settings object whose data folder, tool and model all live in a folder of its own inside the temp folder.</summary>
    private AppSettings Settings(bool memoryDb = true, bool tool = true, bool model = true)
    {
        var root = Path.Combine(_dir, Guid.NewGuid().ToString("N"));
        var s = new AppSettings { DbPath = Path.Combine(root, "data", "context.db"), LlmSearchDir = Path.Combine(root, "models") };
        if (memoryDb) Touch(root, "data", "memory.db");
        if (tool)
        {
            Touch(root, "worker", "memory_llm.py");
            Touch(root, "worker", ".venv", "Scripts", "python.exe");
            s.WorkerDir = Path.Combine(root, "worker");
        }
        if (model) Touch(root, "models", "qwen.gguf");
        return s;
    }

    [Fact]
    public void Progress_lines_are_read_and_everything_else_is_ignored()
    {
        Assert.Equal(new WriterProgress("summaries", 12, 40), MemoryWriter.ParseProgress("PROGRESS {\"stage\":\"summaries\",\"done\":12,\"total\":40}"));
        Assert.Equal(new WriterProgress("notes", 0, 0), MemoryWriter.ParseProgress("PROGRESS {\"stage\":\"notes\",\"done\":0,\"total\":0}"));
        Assert.Null(MemoryWriter.ParseProgress("summaries: {\"waiting\": 3}"));
        Assert.Null(MemoryWriter.ParseProgress("PROGRESS not json"));
        Assert.Null(MemoryWriter.ParseProgress("PROGRESS {\"stage\":\"x\"}"));
    }

    [Fact]
    public void The_result_line_becomes_a_plain_sentence()
    {
        Assert.Equal("Wrote 38 of 40 sentences, then refreshed session notes and day recaps.",
            MemoryWriter.DescribeSummary("summaries: {\"waiting\": 40, \"summarized\": 38, \"discarded\": 2, \"failed\": 0}"));
        Assert.StartsWith("Nothing was waiting", MemoryWriter.DescribeSummary("summaries: {\"waiting\": 0, \"summarized\": 0}"));
        Assert.Equal("Finished.", MemoryWriter.DescribeSummary("garbled"));
    }

    [Fact]
    public void With_everything_in_place_there_is_no_problem_and_the_command_names_the_files()
    {
        var s = Settings();
        var writer = new MemoryWriter(s, _dir);

        Assert.Null(writer.Problem());
        var (file, args, workDir) = writer.Command(25)!.Value;

        Assert.EndsWith(Path.Combine(".venv", "Scripts", "python.exe"), file);
        Assert.Equal(s.WorkerDir, workDir);
        Assert.Contains("memory_llm.py", args);
        Assert.Contains($"--memory-db \"{s.MemoryPath}\"", args);
        Assert.Contains("qwen.gguf", args);
        Assert.EndsWith("--max-entries 25", args);
    }

    [Fact]
    public void Each_missing_piece_is_named()
    {
        Assert.Contains("no memory database", new MemoryWriter(Settings(memoryDb: false), _dir).Problem());
        Assert.Contains("No language model file", new MemoryWriter(Settings(model: false), _dir).Problem());
        Assert.Contains("repo's Python", new MemoryWriter(Settings(tool: false), _dir).Problem());
    }

    [Fact]
    public void A_missing_tool_gives_no_command_and_running_does_nothing()
    {
        var writer = new MemoryWriter(Settings(tool: false), _dir);

        Assert.Null(writer.Command(10));
        var message = writer.RunAsync(10, _ => { }).GetAwaiter().GetResult();
        Assert.Contains("repo's Python", message);
        Assert.False(writer.Running);
    }
}

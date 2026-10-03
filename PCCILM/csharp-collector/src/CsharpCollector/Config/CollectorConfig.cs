using System.IO;
using System.Text.Json;

namespace CsharpCollector.Config;

internal sealed class CollectorConfig
{
    public List<string> ExcludedApps { get; init; } = [];
    public List<string> ExcludedDomains { get; init; } = [];
    public List<string> BrowserProcesses { get; init; } = ["chrome", "msedge"];
    public int IdleThresholdSeconds { get; init; } = 60;
    public int ActivityWindowSeconds { get; init; } = 10;
    public int ActivityEmitIntervalSeconds { get; init; } = 10;
    public ScreenCaptureConfig ScreenCapture { get; init; } = new();
    public ScreenContentConfig ScreenContent { get; init; } = new();
    public IpcConfig Ipc { get; init; } = new();

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };

    // A missing config is an error rather than "no exclusions": silently running without the
    // user's exclusion list would sense exactly the apps they asked us not to.
    public static CollectorConfig Load(string path)
    {
        if (!File.Exists(path))
            throw new FileNotFoundException($"Config file not found: {path}", path);

        var config = JsonSerializer.Deserialize<CollectorConfig>(File.ReadAllText(path), JsonOptions)
                     ?? throw new InvalidDataException($"Config file is empty: {path}");
        config.Validate();
        return config;
    }

    private void Validate()
    {
        if (IdleThresholdSeconds <= 0)
            throw new InvalidDataException("idleThresholdSeconds must be > 0");
        if (ActivityWindowSeconds <= 0)
            throw new InvalidDataException("activityWindowSeconds must be > 0");
        if (ActivityEmitIntervalSeconds <= 0)
            throw new InvalidDataException("activityEmitIntervalSeconds must be > 0");
        if (ScreenCapture.ProbeIntervalMs < 50)
            throw new InvalidDataException("screenCapture.probeIntervalMs must be >= 50");
        if (ScreenCapture.DiffThreshold is < 0 or > 255)
            throw new InvalidDataException("screenCapture.diffThreshold must be within 0-255");
        if (ScreenCapture.BufferCapacity < 1)
            throw new InvalidDataException("screenCapture.bufferCapacity must be >= 1");
        if (ScreenContent.MaxChars < 1)
            throw new InvalidDataException("screenContent.maxChars must be >= 1");
        if (ScreenContent.MaxNodes < 1)
            throw new InvalidDataException("screenContent.maxNodes must be >= 1");
        if (ScreenContent.TimeBudgetMs < 1)
            throw new InvalidDataException("screenContent.timeBudgetMs must be >= 1");
        if (ScreenContent.MinImageSide < 1)
            throw new InvalidDataException("screenContent.minImageSide must be >= 1");
        if (!string.Equals(Ipc.Transport, "namedpipe", StringComparison.OrdinalIgnoreCase) &&
            !string.Equals(Ipc.Transport, "tcp", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("ipc.transport must be 'namedpipe' or 'tcp'");
        if (Ipc.TcpPort is < 1 or > 65535)
            throw new InvalidDataException("ipc.tcpPort must be within 1-65535");
    }
}

internal sealed class IpcConfig
{
    public bool Enabled { get; init; } = true;
    public string Transport { get; init; } = "namedpipe";
    public string PipeName { get; init; } = "context-importance";
    public int TcpPort { get; init; } = 51789;
    public bool SendFrameBytes { get; init; } = true;
}

/// <summary>
/// Reading the visible text and large images of the foreground window (UI Automation). Off by
/// default: it reads screen content, which participants' consent must cover first.
/// </summary>
internal sealed class ScreenContentConfig
{
    public bool Enabled { get; init; }
    public int MaxChars { get; init; } = 50_000;
    public int MaxNodes { get; init; } = 3_000;
    public int TimeBudgetMs { get; init; } = 400;
    public int MinImageSide { get; init; } = 64;

    public Content.ContentLimits ToLimits() => new(MaxChars, MaxNodes, TimeBudgetMs, MinImageSide);
}

internal sealed class ScreenCaptureConfig
{
    public bool Enabled { get; init; } = true;
    public int ProbeIntervalMs { get; init; } = 500;
    public double DiffThreshold { get; init; } = 3.0;
    public int BufferCapacity { get; init; } = 1;
}

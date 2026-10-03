using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using CsharpCollector.Browser;
using CsharpCollector.Capture;
using CsharpCollector.Config;
using CsharpCollector.Content;
using CsharpCollector.Filtering;
using CsharpCollector.Native;
using CsharpCollector.Sensing;

namespace CsharpCollector.Benchmarks;

/// <summary>
/// Measures reading screen content (visible text + large images, UI Automation) on the real apps
/// in front of you. Every <c>--interval-ms</c> it reads the foreground window, after the same
/// exclusion checks as the collector, and records only counts and timings: the text itself is
/// never printed or saved. Switch between apps (Edge, Chrome, VS Code, Word, Explorer...) while it runs.
///
///   dotnet run -c Release --project benchmarks/CsharpCollector.Benchmarks -- --content [--samples 30] [--interval-ms 2000]
/// </summary>
internal static class ContentReadBenchmark
{
    private sealed record Sample(DateTime At, string App, string Outcome, double ReadMs, int Nodes, int Chars, int Images, bool Truncated);

    public static int Run(string[] args, Func<string[], string, int, int> intArg, Func<string[], string, string, string> stringArg)
    {
        var samples = intArg(args, "--samples", 30);
        var intervalMs = intArg(args, "--interval-ms", 2000);
        var outDir = stringArg(args, "--out", "benchmark-results");
        var configPath = stringArg(args, "--config",
            Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "src", "CsharpCollector", "collector.config.json"));

        DpiAwareness.EnablePerMonitorV2();
        var config = CollectorConfig.Load(configPath);
        var filter = new ExclusionFilter(config.ExcludedApps, config.ExcludedDomains);
        var browsers = new HashSet<string>(config.BrowserProcesses.Select(ExclusionFilter.NormalizeAppName), StringComparer.OrdinalIgnoreCase);
        var windows = new Win32WindowInfoSource();
        var urls = new UiaBrowserUrlReader();
        var reader = new UiaScreenContentReader(config.ScreenContent.ToLimits());

        Console.Error.WriteLine($"Reading the foreground window {samples} times, every {intervalMs} ms. Switch between apps now.");
        Console.Error.WriteLine("Only counts and timings are recorded; no screen text is printed or saved.");
        var results = new List<Sample>();
        for (var i = 0; i < samples; i++)
        {
            Thread.Sleep(intervalMs);
            var hwnd = NativeMethods.GetForegroundWindow();
            var app = windows.GetProcessName(hwnd);
            Sample sample;
            if (filter.IsAppExcluded(app))
                sample = new Sample(DateTime.Now, app ?? "(unknown)", "excluded app: not read", 0, 0, 0, 0, false);
            else if (browsers.Contains(app!) && filter.IsUrlExcluded(urls.TryReadUrl(hwnd)))
                sample = new Sample(DateTime.Now, app!, "excluded domain: not read", 0, 0, 0, 0, false);
            else if (reader.TryRead(hwnd) is { } content)
                sample = new Sample(DateTime.Now, app!, "read", content.ReadMs, content.NodesVisited,
                    content.Text.Length, content.Images.Count, content.Truncated);
            else
                sample = new Sample(DateTime.Now, app!, "UIA failed", 0, 0, 0, 0, false);

            results.Add(sample);
            Console.Error.WriteLine(string.Format(CultureInfo.InvariantCulture,
                "{0,3}  {1,-22} {2,-26} {3,7:F1} ms  {4,5} nodes  {5,6} chars  {6,2} images{7}",
                i + 1, Short(sample.App), sample.Outcome, sample.ReadMs, sample.Nodes, sample.Chars, sample.Images,
                sample.Truncated ? "  (truncated)" : ""));
        }

        var report = BuildReport(results, config.ScreenContent);
        Console.WriteLine(report);
        Directory.CreateDirectory(outDir);
        var path = Path.Combine(outDir, $"content-read-{DateTime.Now:yyyyMMdd-HHmmss}.md");
        File.WriteAllText(path, report);
        Console.Error.WriteLine($"Saved {Path.GetFullPath(path)}");
        return 0;
    }

    private static string BuildReport(List<Sample> results, ScreenContentConfig limits)
    {
        var inv = CultureInfo.InvariantCulture;
        var sb = new StringBuilder();
        sb.AppendLine("# Screen content read (UI Automation): latency per app");
        sb.AppendLine();
        sb.AppendLine($"Date {DateTime.Now:yyyy-MM-dd HH:mm} · limits: {limits.TimeBudgetMs} ms, {limits.MaxNodes} nodes, " +
                      $"{limits.MaxChars} chars, images ≥ {limits.MinImageSide} px · {BuildConfiguration} build");
        sb.AppendLine();
        sb.AppendLine("| App | reads | p50 (ms) | p95 (ms) | max (ms) | median chars | median images | truncated | not read |");
        sb.AppendLine("|---|---:|---:|---:|---:|---:|---:|---:|---:|");
        foreach (var group in results.GroupBy(r => r.App).OrderByDescending(g => g.Count()))
        {
            var read = group.Where(r => r.Outcome == "read").ToArray();
            var ms = read.Select(r => r.ReadMs).Order().ToArray();
            sb.AppendLine(string.Format(inv, "| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} | {8} |",
                group.Key, read.Length,
                Fmt(ms, 50), Fmt(ms, 95), ms.Length > 0 ? ms[^1].ToString("F1", inv) : "–",
                Median(read.Select(r => (double)r.Chars)), Median(read.Select(r => (double)r.Images)),
                read.Count(r => r.Truncated), group.Count() - read.Length));
        }
        sb.AppendLine();
        sb.AppendLine("Only counts and timings are recorded; the screen text itself is never printed or saved.");
#if DEBUG
        sb.AppendLine().AppendLine("> Warning: Debug build. Rerun with `-c Release` before reporting these numbers.");
#endif
        return sb.ToString();
    }

    private static string Fmt(double[] sorted, int p) =>
        sorted.Length == 0 ? "–" : sorted[Math.Clamp((int)Math.Ceiling(p / 100.0 * sorted.Length) - 1, 0, sorted.Length - 1)]
            .ToString("F1", CultureInfo.InvariantCulture);

    private static string Median(IEnumerable<double> values)
    {
        var s = values.Order().ToArray();
        return s.Length == 0 ? "–" : s[s.Length / 2].ToString("F0", CultureInfo.InvariantCulture);
    }

    private static string Short(string app) => app.Length <= 22 ? app : app[..21] + "…";

#if DEBUG
    private const string BuildConfiguration = "Debug";
#else
    private const string BuildConfiguration = "Release";
#endif

    /// <summary>
    /// <c>--trace</c>: one read of the foreground window with every UIA call timed, to find which
    /// node or call is slow. Prints node kinds and timings only, never names or text.
    /// </summary>
    public static int Trace(string[] args, Func<string[], string, int, int> intArg)
    {
        var delayMs = intArg(args, "--delay-ms", 3000);
        DpiAwareness.EnablePerMonitorV2();
        Console.Error.WriteLine($"Bring the window to measure to the front; reading in {delayMs} ms...");
        Thread.Sleep(delayMs);
        var hwnd = NativeMethods.GetForegroundWindow();
        Console.Error.WriteLine($"app: {new Win32WindowInfoSource().GetProcessName(hwnd)}");

        var log = new List<(string Call, double Ms)>();
        var t0 = Stopwatch.GetTimestamp();
        var root = new TimedNode(Timed(log, "FromHandle", () => UiaContentNode.FromHandle(hwnd)), log, depth: 0);
        var content = ScreenContentExtractor.Extract(root, new ContentLimits(TimeBudgetMs: 60_000), () => Stopwatch.GetElapsedTime(t0));

        Console.Error.WriteLine($"total {content.ReadMs:F1} ms, {content.NodesVisited} nodes, {content.Text.Length} chars, {content.Images.Count} images");
        Console.Error.WriteLine("slowest calls:");
        foreach (var (call, ms) in log.OrderByDescending(l => l.Ms).Take(15))
            Console.Error.WriteLine($"  {ms,8:F1} ms  {call}");
        Console.Error.WriteLine($"calls: {log.Count}, summed {log.Sum(l => l.Ms):F1} ms");
        return 0;
    }

    private static T Timed<T>(List<(string, double)> log, string call, Func<T> action)
    {
        var start = Stopwatch.GetTimestamp();
        try
        {
            return action();
        }
        finally
        {
            log.Add((call, Stopwatch.GetElapsedTime(start).TotalMilliseconds));
        }
    }

    private sealed class TimedNode(IContentNode inner, List<(string, double)> log, int depth) : IContentNode
    {
        private string Label(string call) => $"{call} on {inner.Kind} (depth {depth})";
        public ContentKind Kind => Timed(log, $"Kind (depth {depth})", () => inner.Kind);
        public string? Name => Timed(log, Label("Name"), () => inner.Name);
        public ScreenRect Bounds => Timed(log, Label("Bounds"), () => inner.Bounds);
        public bool IsOffscreen => Timed(log, Label("IsOffscreen"), () => inner.IsOffscreen);
        public bool IsPassword => Timed(log, Label("IsPassword"), () => inner.IsPassword);
        public bool HasKeyboardFocus => Timed(log, Label("HasKeyboardFocus"), () => inner.HasKeyboardFocus);
        public string? TryGetValue() => Timed(log, Label("Value"), inner.TryGetValue);
        public string? TryGetVisibleText(int maxChars) => Timed(log, Label("VisibleText"), () => inner.TryGetVisibleText(maxChars));
        public IReadOnlyList<IContentNode>? FindVisibleContent() =>
            Timed(log, Label("FindVisibleContent"), inner.FindVisibleContent)?.Select(c => (IContentNode)new TimedNode(c, log, depth + 1)).ToList();
        public IReadOnlyList<IContentNode> GetChildren() =>
            Timed(log, Label("GetChildren"), inner.GetChildren).Select(c => (IContentNode)new TimedNode(c, log, depth + 1)).ToList();
    }
}

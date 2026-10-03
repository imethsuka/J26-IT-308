using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using CsharpCollector.Capture;
using CsharpCollector.Native;
using Microsoft.Win32;

namespace CsharpCollector.Benchmarks;

/// <summary>
/// Measures the real capture pipeline on this machine: screen grab, thumbnail, diff, and the
/// store on trigger. Uses wall-clock Stopwatch timing because GDI and DWM readback cost can't be
/// micro-benchmarked in isolation. Prints a Markdown report and saves it under benchmark-results/.
/// </summary>
internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Contains("--trace"))
            return ContentReadBenchmark.Trace(args, IntArg);
        if (args.Contains("--content"))
            return ContentReadBenchmark.Run(args, IntArg, StringArg);

        var iterations = IntArg(args, "--iterations", 200);
        var warmup = IntArg(args, "--warmup", 20);
        var outDir = StringArg(args, "--out", "benchmark-results");

        DpiAwareness.EnablePerMonitorV2();
        using var capturer = new GdiScreenCapturer();

        var first = capturer.Capture();
        if (first is null)
        {
            Console.Error.WriteLine("Screen capture failed. Is the session locked or on the secure desktop?");
            return 1;
        }
        var baseline = Thumbnail.FromFrame(first);
        var buffer = new FrameBuffer();

        // Same steps, in the same order, as ScreenChangeMonitor.ProbeOnce. Every iteration takes
        // the trigger path so both totals come from the same samples.
        var stages = new[]
        {
            "Screen grab (GDI BitBlt + copy to reused buffer)",
            "64x36 greyscale downscale",
            "Mean absolute difference",
            "**Probe total** (paid every probe interval)",
            "Store on trigger (copy frame into buffer)",
            "**Trigger total** (probe + store)",
        };
        var samples = stages.Select(_ => new List<double>(iterations)).ToArray();

        Console.Error.WriteLine($"Running {warmup} warmup + {iterations} measured iterations...");
        for (var i = 0; i < warmup + iterations; i++)
        {
            var t0 = Stopwatch.GetTimestamp();
            var grab = capturer.Capture()!;
            var t1 = Stopwatch.GetTimestamp();
            var thumb = Thumbnail.FromFrame(grab);
            var t2 = Stopwatch.GetTimestamp();
            Thumbnail.MeanAbsoluteDifference(thumb, baseline);
            var t3 = Stopwatch.GetTimestamp();
            buffer.Add(grab.Clone());
            var t4 = Stopwatch.GetTimestamp();

            if (i < warmup)
                continue;
            samples[0].Add(Ms(t0, t1));
            samples[1].Add(Ms(t1, t2));
            samples[2].Add(Ms(t2, t3));
            samples[3].Add(Ms(t0, t3));
            samples[4].Add(Ms(t3, t4));
            samples[5].Add(Ms(t0, t4));
        }

        var report = BuildReport(stages, samples, iterations, warmup);
        Console.WriteLine(report);

        Directory.CreateDirectory(outDir);
        var path = Path.Combine(outDir, $"capture-latency-{DateTime.Now:yyyyMMdd-HHmmss}.md");
        File.WriteAllText(path, report);
        Console.Error.WriteLine($"Saved {Path.GetFullPath(path)}");
        return 0;
    }

    private static string BuildReport(string[] stages, List<double>[] samples, int iterations, int warmup)
    {
        var (width, height) = GdiScreenCapturer.PrimaryScreenSize();
        var scale = NativeMethods.GetDpiForSystem() * 100 / 96;
        var inv = CultureInfo.InvariantCulture;

        var sb = new StringBuilder();
        sb.AppendLine("# Screen capture + diff latency");
        sb.AppendLine();
        sb.AppendLine("| | |");
        sb.AppendLine("|---|---|");
        sb.AppendLine($"| Date | {DateTime.Now:yyyy-MM-dd HH:mm} |");
        sb.AppendLine($"| CPU | {CpuName()} ({Environment.ProcessorCount} logical cores) |");
        sb.AppendLine($"| OS | {RuntimeInformation.OSDescription} |");
        sb.AppendLine($"| Runtime | {RuntimeInformation.FrameworkDescription} |");
        sb.AppendLine($"| Primary monitor | {width}x{height} physical px, {scale}% scale |");
        sb.AppendLine($"| Capture API | GDI BitBlt, primary monitor |");
        sb.AppendLine($"| Build | {BuildConfiguration} |");
        sb.AppendLine($"| Samples | {iterations} measured after {warmup} warmup |");
        sb.AppendLine();
        sb.AppendLine("| Stage | mean (ms) | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) |");
        sb.AppendLine("|---|---:|---:|---:|---:|---:|");
        for (var i = 0; i < stages.Length; i++)
        {
            var s = samples[i].Order().ToArray();
            sb.AppendLine(string.Format(inv, "| {0} | {1:F3} | {2:F3} | {3:F3} | {4:F3} | {5:F3} |",
                stages[i], s.Average(), Percentile(s, 50), Percentile(s, 95), Percentile(s, 99), s[^1]));
        }
        sb.AppendLine();
        sb.AppendLine("Probe total is paid on every probe. Trigger total is paid only when the diff crosses the threshold.");
#if DEBUG
        sb.AppendLine().AppendLine("> Warning: Debug build. Rerun with `-c Release` before reporting these numbers.");
#endif
        return sb.ToString();
    }

    private static double Ms(long start, long end) => (end - start) * 1000.0 / Stopwatch.Frequency;

    private static double Percentile(double[] sorted, int p) =>
        sorted[Math.Clamp((int)Math.Ceiling(p / 100.0 * sorted.Length) - 1, 0, sorted.Length - 1)];

    private static string CpuName() =>
        Registry.GetValue(@"HKEY_LOCAL_MACHINE\HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString", null)
            is string name ? name.Trim() : "unknown";

#if DEBUG
    private const string BuildConfiguration = "Debug";
#else
    private const string BuildConfiguration = "Release";
#endif

    private static int IntArg(string[] args, string name, int fallback)
    {
        var i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length && int.TryParse(args[i + 1], out var v) && v > 0 ? v : fallback;
    }

    private static string StringArg(string[] args, string name, string fallback)
    {
        var i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback;
    }
}

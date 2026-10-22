using System.Diagnostics;

namespace ContextDashboard.Services;

/// <summary>
/// What an installed program understands. An older frozen worker or job rejects flags it does not know and exits, so new
/// options are only passed when the program's own <c>--help</c> lists them. The answer is cached per program file (and its
/// timestamp) and arguments, so <c>--help</c> runs once per build.
/// </summary>
internal static class HelpProbe
{
    private static readonly Dictionary<string, string> Cache = [];

    public static bool Supports(Func<string, string, string> help, string file, string prefix, string flag)
    {
        var key = file + "|" + prefix + "|" + (File.Exists(file) ? File.GetLastWriteTimeUtc(file).Ticks : 0);
        string text;
        lock (Cache)
        {
            if (!Cache.TryGetValue(key, out text!))
                Cache[key] = text = help(file, prefix);
        }
        return text.Contains(flag, StringComparison.Ordinal);
    }

    public static string Run(string file, string prefix)
    {
        try
        {
            var info = new ProcessStartInfo(file, (prefix + " --help").Trim())
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
            };
            using var process = Process.Start(info);
            if (process is null) return "";
            var output = process.StandardOutput.ReadToEndAsync();
            var errors = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(30_000)) { try { process.Kill(true); } catch (InvalidOperationException) { } return ""; }
            return output.Result + errors.Result;
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException or IOException)
        {
            return "";
        }
    }
}

namespace CsharpCollector.Config;

/// <summary>Command-line switches that override <c>collector.config.json</c>.</summary>
internal static class CommandLine
{
    /// <summary>
    /// <c>--screen-text on|off</c>: the dashboard states the user's choice on every start, so the choice made in the
    /// dashboard wins over the (read-only, installed) config file in both directions. Null if absent or not on/off,
    /// in which case the config file decides (off by default).
    /// </summary>
    public static bool? ScreenText(string[] args)
    {
        var index = Array.IndexOf(args, "--screen-text");
        if (index < 0 || index + 1 >= args.Length) return null;
        return args[index + 1].ToLowerInvariant() switch
        {
            "on" => true,
            "off" => false,
            _ => null,
        };
    }
}

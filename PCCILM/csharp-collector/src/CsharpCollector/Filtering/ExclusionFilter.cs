using System.IO;

namespace CsharpCollector.Filtering;

/// <summary>
/// Pure exclusion rules for app names and URL domains. It fails closed: anything it cannot
/// identify is treated as excluded.
/// </summary>
internal sealed class ExclusionFilter
{
    private readonly HashSet<string> _apps;
    private readonly string[] _domains;

    public ExclusionFilter(IEnumerable<string> excludedApps, IEnumerable<string> excludedDomains)
    {
        _apps = new HashSet<string>(
            excludedApps.Select(NormalizeAppName).Where(a => a.Length > 0),
            StringComparer.OrdinalIgnoreCase);
        _domains = excludedDomains.Select(NormalizeDomain).Where(d => d.Length > 0).Distinct().ToArray();
    }

    public bool HasDomainRules => _domains.Length > 0;

    /// <summary>An unknown process (null) is excluded, because we can't prove it isn't on the list.</summary>
    public bool IsAppExcluded(string? processName) =>
        processName is null || _apps.Contains(NormalizeAppName(processName));

    /// <summary>
    /// True if the URL's host is an excluded domain or one of its subdomains. A null or
    /// unparseable URL counts as excluded whenever domain rules exist.
    /// </summary>
    public bool IsUrlExcluded(string? url)
    {
        if (!HasDomainRules)
            return false;

        var host = TryGetHost(url);
        if (host is null)
            return true;

        foreach (var domain in _domains)
        {
            if (host == domain || host.EndsWith("." + domain, StringComparison.Ordinal))
                return true;
        }
        return false;
    }

    internal static string NormalizeAppName(string name)
    {
        var trimmed = Path.GetFileName(name.Trim());
        return trimmed.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) ? trimmed[..^4] : trimmed;
    }

    internal static string NormalizeDomain(string domain)
    {
        var d = domain.Trim();
        if (d.Contains("://"))
            d = TryGetHost(d) ?? "";
        return d.TrimStart('*').Trim('.').ToLowerInvariant();
    }

    // Chromium hides the scheme in the address bar ("github.com/x"), so assume https when it's missing.
    internal static string? TryGetHost(string? url)
    {
        if (string.IsNullOrWhiteSpace(url))
            return null;

        var candidate = url.Trim();
        if (!candidate.Contains("://"))
            candidate = "https://" + candidate;

        if (!Uri.TryCreate(candidate, UriKind.Absolute, out var uri) || string.IsNullOrEmpty(uri.Host))
            return null;

        return uri.IdnHost.TrimEnd('.').ToLowerInvariant();
    }
}

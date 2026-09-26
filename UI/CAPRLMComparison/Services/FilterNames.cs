namespace CAPRLMComparison.Services;

/// <summary>The five filters the service compares, in display order (CAPRLM first, then the baselines).</summary>
public static class FilterNames
{
    public static readonly string[] Order =
        ["caprlm", "rule_based", "static_masking", "anonymization", "pseudonymization"];
}

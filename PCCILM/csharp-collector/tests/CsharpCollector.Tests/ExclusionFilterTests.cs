using CsharpCollector.Filtering;

namespace CsharpCollector.Tests;

public class ExclusionFilterTests
{
    private static ExclusionFilter Filter(string[]? apps = null, string[]? domains = null) =>
        new(apps ?? [], domains ?? []);

    [Theory]
    [InlineData("KeePass")]
    [InlineData("keepass")]
    [InlineData("KEEPASS")]
    public void App_match_is_case_insensitive(string process) =>
        Assert.True(Filter(apps: ["KeePass"]).IsAppExcluded(process));

    [Theory]
    [InlineData("KeePass.exe")]
    [InlineData(@"C:\Program Files\KeePass\KeePass.exe")]
    [InlineData("  KeePass  ")]
    public void Config_app_names_are_normalized(string configured) =>
        Assert.True(Filter(apps: [configured]).IsAppExcluded("KeePass"));

    [Fact]
    public void App_not_in_list_is_allowed() =>
        Assert.False(Filter(apps: ["KeePass"]).IsAppExcluded("notepad"));

    [Fact]
    public void App_name_is_not_prefix_matched() =>
        Assert.False(Filter(apps: ["Keep"]).IsAppExcluded("KeePass"));

    [Fact]
    public void Unknown_process_fails_closed() =>
        Assert.True(Filter().IsAppExcluded(null));

    [Theory]
    [InlineData("https://bank.com/login")]
    [InlineData("https://www.bank.com/")]
    [InlineData("https://a.b.bank.com/x?y=1")]
    [InlineData("bank.com/accounts")]
    [InlineData("HTTPS://BANK.COM")]
    [InlineData("https://bank.com.:443/")]
    public void Domain_and_subdomains_are_excluded(string url) =>
        Assert.True(Filter(domains: ["bank.com"]).IsUrlExcluded(url));

    [Theory]
    [InlineData("https://notbank.com/")]
    [InlineData("https://bank.com.evil.io/")]
    [InlineData("https://evil.io/?next=bank.com")]
    [InlineData("https://evil.io/bank.com")]
    public void Lookalike_domains_are_not_excluded(string url) =>
        Assert.False(Filter(domains: ["bank.com"]).IsUrlExcluded(url));

    [Theory]
    [InlineData("*.bank.com")]
    [InlineData(".bank.com")]
    [InlineData("BANK.COM")]
    [InlineData("https://bank.com/some/path")]
    public void Config_domains_are_normalized(string configured) =>
        Assert.True(Filter(domains: [configured]).IsUrlExcluded("https://www.bank.com/"));

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("not a url at all")]
    public void Unreadable_url_fails_closed_when_domain_rules_exist(string? url) =>
        Assert.True(Filter(domains: ["bank.com"]).IsUrlExcluded(url));

    [Theory]
    [InlineData(null)]
    [InlineData("not a url at all")]
    public void Unreadable_url_is_allowed_when_no_domain_rules(string? url) =>
        Assert.False(Filter().IsUrlExcluded(url));
}

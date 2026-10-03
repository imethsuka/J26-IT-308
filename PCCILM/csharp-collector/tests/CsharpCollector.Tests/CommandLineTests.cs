using CsharpCollector.Config;

namespace CsharpCollector.Tests;

public class CommandLineTests
{
    [Theory]
    [InlineData("on", true)]
    [InlineData("ON", true)]
    [InlineData("off", false)]
    public void Screen_text_switch_is_read(string value, bool expected) =>
        Assert.Equal(expected, CommandLine.ScreenText(["--config", "x.json", "--screen-text", value]));

    [Theory]
    [InlineData]
    [InlineData("--screen-text")]
    [InlineData("--screen-text", "maybe")]
    [InlineData("--screen-text", "1")]
    public void Missing_or_unclear_switch_leaves_the_decision_to_the_config(params string[] args) =>
        Assert.Null(CommandLine.ScreenText(args));
}

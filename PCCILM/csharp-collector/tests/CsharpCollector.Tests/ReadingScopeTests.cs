using CsharpCollector.Content;

namespace CsharpCollector.Tests;

public class ReadingScopeTests
{
    private static readonly ScreenRect Window = new(0, 0, 1920, 1020);

    [Fact]
    public void Focus_in_a_side_pane_reads_only_that_pane()
    {
        // VS Code with the chat focused, as measured: input, groups, five nested chat documents, then the main workbench.
        var chain = new List<(bool, ScreenRect)>
        {
            (false, new(1300, 900, 575, 50)), (false, new(1300, 800, 577, 127)), (false, new(1300, 100, 617, 903)),
            (true, new(1300, 100, 618, 903)), (true, new(1300, 100, 618, 903)), (true, new(1300, 100, 618, 903)),
            (false, new(1300, 100, 617, 902)), (false, Window), (true, Window), (false, Window),
        };
        Assert.Equal(5, ReadingScope.Choose(Window, chain));
    }

    [Fact]
    public void Focus_in_a_browser_page_reads_the_whole_window()
    {
        var chain = new List<(bool, ScreenRect)> { (false, new(10, 200, 300, 20)), (true, new(0, 120, 1920, 900)), (false, Window) };
        Assert.Null(ReadingScope.Choose(Window, chain));
    }

    [Fact]
    public void No_document_on_the_way_up_reads_the_whole_window()
    {
        var chain = new List<(bool, ScreenRect)> { (false, new(0, 0, 200, 30)), (false, Window) };
        Assert.Null(ReadingScope.Choose(Window, chain));
        Assert.Null(ReadingScope.Choose(default, chain));
    }
}

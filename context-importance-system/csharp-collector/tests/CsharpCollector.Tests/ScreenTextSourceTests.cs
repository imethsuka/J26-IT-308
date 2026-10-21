using System.Text;
using System.Text.Json;
using CsharpCollector.Content;
using CsharpCollector.Events;
using CsharpCollector.Ipc;
using CsharpCollector.Sensing;

namespace CsharpCollector.Tests;

internal sealed class FakeContentReader : IScreenContentReader
{
    public List<IntPtr> Reads { get; } = [];
    public Action? OnRead { get; set; }
    public ScreenContent? Result { get; set; } =
        new(ScreenContent.Uia, "hello world", [new ContentImage(new ScreenRect(10, 20, 300, 200), "a red shirt")], false, 5, 12.34);

    public ScreenContent? TryRead(IntPtr hwnd)
    {
        Reads.Add(hwnd);
        OnRead?.Invoke();
        return Result;
    }
}

public class ForegroundScreenTextSourceTests
{
    private static readonly DateTimeOffset T0 = DateTimeOffset.Parse("2026-09-24T00:00:00Z");

    private static (ForegroundScreenTextSource Source, ContextGate Gate, CollectorState State, FakeContentReader Reader) Make(
        bool allowed = true, bool excluded = false)
    {
        var gate = new ContextGate();
        var state = new CollectorState();
        if (excluded)
            state.Emit(new ExcludedContextEvent(T0));
        else
            state.Emit(new WindowEvent("foreground", T0, "chrome", "Docs", "https://docs.example.com/x"));
        if (allowed)
            gate.MarkAllowed(gate.NextSignal());
        var reader = new FakeContentReader();
        return (new ForegroundScreenTextSource(gate, state, reader, () => new IntPtr(42), () => T0), gate, state, reader);
    }

    [Fact]
    public void Reads_the_foreground_window_when_the_context_is_allowed()
    {
        var (source, _, _, reader) = Make();

        var content = source.TryRead();

        Assert.NotNull(content);
        Assert.Equal(new IntPtr(42), Assert.Single(reader.Reads));
    }

    [Fact]
    public void Excluded_context_is_never_read()
    {
        var (source, _, _, reader) = Make(excluded: true);

        Assert.Null(source.TryRead());
        Assert.Empty(reader.Reads);
    }

    [Fact]
    public void Context_that_is_not_evaluated_yet_is_never_read()
    {
        var (source, _, _, reader) = Make(allowed: false);

        Assert.Null(source.TryRead());
        Assert.Empty(reader.Reads);
    }

    [Fact]
    public void No_foreground_window_is_never_read()
    {
        var gate = new ContextGate();
        var state = new CollectorState();
        state.Emit(new WindowEvent("foreground", T0, "chrome", "Docs", null));
        gate.MarkAllowed(gate.NextSignal());
        var reader = new FakeContentReader();

        Assert.Null(new ForegroundScreenTextSource(gate, state, reader, () => IntPtr.Zero, () => T0).TryRead());
        Assert.Empty(reader.Reads);
    }

    [Fact]
    public void Text_is_dropped_when_the_foreground_changes_during_the_read()
    {
        var (source, gate, _, reader) = Make();
        reader.OnRead = () => gate.NextSignal(); // the user switched window while UI Automation was reading

        Assert.Null(source.TryRead());
        Assert.Single(reader.Reads);
    }

    [Fact]
    public void Failed_read_gives_no_text()
    {
        var (source, _, _, reader) = Make();
        reader.Result = null;

        Assert.Null(source.TryRead());
    }
}

public class SnapshotTextWireTests
{
    private static readonly DateTimeOffset T0 = DateTimeOffset.Parse("2026-09-24T00:00:00Z");

    private static SnapshotPublisher PublisherFor(CollectorState state) =>
        new(state, connection: null!, includeFrameBytes: true);

    private static ScreenContent Content() =>
        new(ScreenContent.Uia, "Oxford Cotton Shirt – LKR 4,990", [new ContentImage(new ScreenRect(420, 180, 600, 600), "Red shirt")], true, 9, 33.33);

    [Fact]
    public void Header_carries_the_text_and_images()
    {
        var state = new CollectorState();
        state.Emit(new WindowEvent("foreground", T0, "chrome", "Shop", "https://shop.example.com/a"));

        var header = PublisherFor(state).Build(SyntheticFrames.Grey(4, 2, 1), null, 1, Content());

        Assert.Equal(2, header.Version);
        Assert.Equal("uia", header.Text!.Source);
        Assert.Equal(Content().Text.Length, header.Text.Chars);
        Assert.True(header.Text.Truncated);
        var image = Assert.Single(header.Text.Images);
        Assert.Equal((420, 180, 600, 600, "Red shirt"), (image.X, image.Y, image.Width, image.Height, image.Alt));
    }

    [Fact]
    public void Text_is_never_sent_for_an_excluded_context_even_if_one_was_read()
    {
        var state = new CollectorState();
        state.Emit(new ExcludedContextEvent(T0));

        var header = PublisherFor(state).Build(SyntheticFrames.Grey(4, 2, 1), null, 1, Content());

        Assert.Null(header.Text);
    }

    [Fact]
    public void Text_serializes_in_snake_case_and_is_omitted_when_absent()
    {
        var state = new CollectorState();
        state.Emit(new WindowEvent("foreground", T0, "chrome", "Shop", null));
        var publisher = PublisherFor(state);

        using var with = JsonDocument.Parse(Encoding.UTF8.GetString(
            WireFormat.SerializeHeader(publisher.Build(SyntheticFrames.Grey(4, 2, 1), null, 1, Content()))));
        var text = with.RootElement.GetProperty("text");
        Assert.Equal("uia", text.GetProperty("source").GetString());
        Assert.Equal(33.3, text.GetProperty("read_ms").GetDouble());
        Assert.Equal("Red shirt", text.GetProperty("images")[0].GetProperty("alt").GetString());
        Assert.Equal(2, with.RootElement.GetProperty("version").GetInt32());

        using var without = JsonDocument.Parse(Encoding.UTF8.GetString(
            WireFormat.SerializeHeader(publisher.Build(SyntheticFrames.Grey(4, 2, 1), null, 2))));
        Assert.False(without.RootElement.TryGetProperty("text", out _));
    }
}

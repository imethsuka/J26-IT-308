using CsharpCollector.Capture;

namespace CsharpCollector.Tests;

public class FrameBufferTests
{
    private static Frame F(byte level) => SyntheticFrames.Grey(64, 36, level);

    [Fact]
    public void Default_is_single_slot_holding_only_the_latest()
    {
        var buffer = new FrameBuffer();
        var first = F(1);
        var second = F(2);

        buffer.Add(first);
        buffer.Add(second);

        Assert.Equal(1, buffer.Capacity);
        Assert.Equal(1, buffer.Count);
        Assert.Same(second, buffer.PeekLatest());
        Assert.Equal(new[] { second }, buffer.Snapshot());
    }

    [Fact]
    public void Larger_capacity_keeps_the_most_recent_frames_oldest_first()
    {
        var buffer = new FrameBuffer(capacity: 3);
        var frames = Enumerable.Range(0, 5).Select(i => F((byte)i)).ToArray();

        foreach (var f in frames)
            buffer.Add(f);

        Assert.Equal(frames[2..], buffer.Snapshot());
        Assert.Same(frames[4], buffer.PeekLatest());
    }

    [Fact]
    public void Empty_buffer_has_no_latest()
    {
        var buffer = new FrameBuffer();
        Assert.Null(buffer.PeekLatest());
        Assert.Empty(buffer.Snapshot());
    }

    [Fact]
    public void Clear_empties_the_buffer()
    {
        var buffer = new FrameBuffer(2);
        buffer.Add(F(1));
        buffer.Add(F(2));

        buffer.Clear();

        Assert.Equal(0, buffer.Count);
        Assert.Null(buffer.PeekLatest());
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Capacity_below_one_is_rejected(int capacity) =>
        Assert.Throws<ArgumentOutOfRangeException>(() => new FrameBuffer(capacity));
}

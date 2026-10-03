using CsharpCollector.Capture;

namespace CsharpCollector.Tests;

public class ScreenChangeDetectorTests
{
    private static byte[] GreyThumb(byte level) => Thumbnail.FromFrame(SyntheticFrames.Grey(640, 360, level));

    [Fact]
    public void First_thumbnail_always_triggers_with_no_difference()
    {
        var decision = new ScreenChangeDetector(3).Evaluate(GreyThumb(0));

        Assert.True(decision.Changed);
        Assert.Null(decision.Difference);
    }

    [Theory]
    [InlineData(2, false)]
    [InlineData(3, true)]   // equal to the threshold counts as changed
    [InlineData(4, true)]
    public void Threshold_boundary(byte level, bool expectedChanged)
    {
        var detector = new ScreenChangeDetector(3);
        detector.Accept(GreyThumb(0));

        var decision = detector.Evaluate(GreyThumb(level));

        Assert.Equal(expectedChanged, decision.Changed);
        Assert.Equal((double)level, decision.Difference);
    }

    [Fact]
    public void Zero_threshold_triggers_on_every_probe()
    {
        var detector = new ScreenChangeDetector(0);
        detector.Accept(GreyThumb(50));
        Assert.True(detector.Evaluate(GreyThumb(50)).Changed);
    }

    [Fact]
    public void Slow_drift_accumulates_against_the_accepted_baseline()
    {
        var detector = new ScreenChangeDetector(3);
        detector.Accept(GreyThumb(100));

        Assert.False(detector.Evaluate(GreyThumb(101)).Changed);
        Assert.False(detector.Evaluate(GreyThumb(102)).Changed);
        Assert.True(detector.Evaluate(GreyThumb(103)).Changed);
    }

    [Fact]
    public void Accept_moves_the_baseline()
    {
        var detector = new ScreenChangeDetector(3);
        detector.Accept(GreyThumb(0));
        detector.Accept(GreyThumb(200));

        Assert.False(detector.Evaluate(GreyThumb(201)).Changed);
    }

    [Fact]
    public void Small_region_change_on_1080p_stays_below_default_threshold()
    {
        var baseFrame = SyntheticFrames.Grey(1920, 1080, 0);
        var detector = new ScreenChangeDetector(3);
        detector.Accept(Thumbnail.FromFrame(baseFrame));

        // A 120x60 block (e.g. a notification badge) is ~0.35% of the screen.
        var decision = detector.Evaluate(Thumbnail.FromFrame(SyntheticFrames.WithRect(baseFrame, 900, 500, 120, 60, 255)));

        Assert.False(decision.Changed);
        Assert.InRange(decision.Difference!.Value, 0.5, 1.5);
    }

    [Fact]
    public void Window_sized_change_on_1080p_triggers()
    {
        var baseFrame = SyntheticFrames.Grey(1920, 1080, 0);
        var detector = new ScreenChangeDetector(3);
        detector.Accept(Thumbnail.FromFrame(baseFrame));

        var decision = detector.Evaluate(Thumbnail.FromFrame(SyntheticFrames.WithRect(baseFrame, 200, 100, 960, 540, 255)));

        Assert.True(decision.Changed);
        Assert.InRange(decision.Difference!.Value, 60, 66); // 25% of pixels changed by 255
    }

    [Theory]
    [InlineData(-0.1)]
    [InlineData(255.1)]
    [InlineData(double.NaN)]
    public void Invalid_threshold_is_rejected(double threshold) =>
        Assert.Throws<ArgumentOutOfRangeException>(() => new ScreenChangeDetector(threshold));
}

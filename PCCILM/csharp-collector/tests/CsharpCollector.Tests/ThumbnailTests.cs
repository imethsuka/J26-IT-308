using CsharpCollector.Capture;

namespace CsharpCollector.Tests;

public class ThumbnailTests
{
    [Fact]
    public void Output_is_64x36_greyscale()
    {
        var thumb = Thumbnail.FromFrame(SyntheticFrames.Grey(1920, 1080, 10));
        Assert.Equal(64 * 36, thumb.Length);
    }

    [Theory]
    [InlineData(255, 255, 255, 255)]
    [InlineData(0, 0, 0, 0)]
    [InlineData(128, 128, 128, 128)]
    [InlineData(255, 0, 0, 76)]   // pure red:   77/256 of full scale
    [InlineData(0, 255, 0, 149)]  // pure green: 150/256
    [InlineData(0, 0, 255, 28)]   // pure blue:  29/256
    public void Solid_colour_maps_to_uniform_luma(byte r, byte g, byte b, byte expected)
    {
        var thumb = Thumbnail.FromFrame(SyntheticFrames.Solid(640, 360, r, g, b));
        Assert.All(thumb, p => Assert.Equal(expected, p));
    }

    [Fact]
    public void Left_white_right_black_splits_cleanly()
    {
        var frame = SyntheticFrames.WithRect(SyntheticFrames.Grey(1280, 720, 0), 0, 0, 640, 720, 255);

        var thumb = Thumbnail.FromFrame(frame);

        for (var y = 0; y < Thumbnail.Height; y++)
        {
            for (var x = 0; x < Thumbnail.Width; x++)
                Assert.Equal(x < 32 ? 255 : 0, thumb[y * Thumbnail.Width + x]);
        }
    }

    [Fact]
    public void Downscale_averages_blocks_instead_of_sampling()
    {
        var thumb = Thumbnail.FromFrame(SyntheticFrames.Checkerboard(128, 72));
        Assert.All(thumb, p => Assert.InRange(p, 127, 128));
    }

    [Theory]
    [InlineData(1366, 768)]
    [InlineData(2560, 1440)]
    [InlineData(3440, 1440)]
    [InlineData(64, 36)]
    public void Non_integer_scale_factors_cover_every_cell(int width, int height)
    {
        var thumb = Thumbnail.FromFrame(SyntheticFrames.Grey(width, height, 200));
        Assert.All(thumb, p => Assert.Equal(200, p));
    }

    [Fact]
    public void Frame_smaller_than_thumbnail_is_rejected() =>
        Assert.Throws<ArgumentException>(() => Thumbnail.FromFrame(SyntheticFrames.Grey(32, 18, 0)));

    [Fact]
    public void Mad_of_identical_thumbnails_is_zero()
    {
        var a = Thumbnail.FromFrame(SyntheticFrames.Checkerboard(640, 360));
        Assert.Equal(0, Thumbnail.MeanAbsoluteDifference(a, a));
    }

    [Fact]
    public void Mad_is_symmetric_and_uses_absolute_values()
    {
        var dark = Thumbnail.FromFrame(SyntheticFrames.Grey(640, 360, 10));
        var light = Thumbnail.FromFrame(SyntheticFrames.Grey(640, 360, 30));

        Assert.Equal(20, Thumbnail.MeanAbsoluteDifference(dark, light));
        Assert.Equal(20, Thumbnail.MeanAbsoluteDifference(light, dark));
    }

    [Fact]
    public void Mad_averages_over_all_pixels()
    {
        var black = Thumbnail.FromFrame(SyntheticFrames.Grey(1280, 720, 0));
        var halfWhite = Thumbnail.FromFrame(SyntheticFrames.WithRect(SyntheticFrames.Grey(1280, 720, 0), 0, 0, 640, 720, 255));

        Assert.Equal(127.5, Thumbnail.MeanAbsoluteDifference(black, halfWhite));
    }

    [Fact]
    public void Mad_rejects_mismatched_lengths() =>
        Assert.Throws<ArgumentException>(() => Thumbnail.MeanAbsoluteDifference(new byte[10], new byte[11]));
}

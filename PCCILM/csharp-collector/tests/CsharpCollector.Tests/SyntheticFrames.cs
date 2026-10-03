using CsharpCollector.Capture;

namespace CsharpCollector.Tests;

/// <summary>Builds BGRA test bitmaps in memory.</summary>
internal static class SyntheticFrames
{
    public static Frame Solid(int width, int height, byte r, byte g, byte b)
    {
        var pixels = new byte[width * height * 4];
        for (var i = 0; i < pixels.Length; i += 4)
        {
            pixels[i] = b;
            pixels[i + 1] = g;
            pixels[i + 2] = r;
            pixels[i + 3] = 255;
        }
        return new Frame(width, height, pixels, DateTimeOffset.UnixEpoch);
    }

    public static Frame Grey(int width, int height, byte level) => Solid(width, height, level, level, level);

    /// <summary>Copy of <paramref name="frame"/> with a filled rectangle drawn on it.</summary>
    public static Frame WithRect(Frame frame, int x, int y, int w, int h, byte level)
    {
        var pixels = (byte[])frame.Pixels.Clone();
        for (var row = y; row < y + h; row++)
        {
            for (var col = x; col < x + w; col++)
            {
                var i = (row * frame.Width + col) * 4;
                pixels[i] = pixels[i + 1] = pixels[i + 2] = level;
            }
        }
        return new Frame(frame.Width, frame.Height, pixels, frame.CapturedAt);
    }

    public static Frame Checkerboard(int width, int height)
    {
        var frame = Grey(width, height, 0);
        var pixels = frame.Pixels;
        for (var y = 0; y < height; y++)
        {
            for (var x = 0; x < width; x++)
            {
                if ((x + y) % 2 == 0)
                {
                    var i = (y * width + x) * 4;
                    pixels[i] = pixels[i + 1] = pixels[i + 2] = 255;
                }
            }
        }
        return frame;
    }
}

namespace CsharpCollector.Capture;

/// <summary>64x36 greyscale thumbnails and their mean absolute difference (0-255 scale).</summary>
internal static class Thumbnail
{
    public const int Width = 64;
    public const int Height = 36;

    /// <summary>Box-filter downscale: each thumbnail pixel is the mean luma of its source block.</summary>
    public static byte[] FromFrame(Frame frame)
    {
        if (frame.Width < Width || frame.Height < Height)
            throw new ArgumentException($"Frame must be at least {Width}x{Height}.", nameof(frame));

        var sums = new int[Width * Height];
        var counts = new int[Width * Height];
        var column = new int[frame.Width];
        for (var x = 0; x < frame.Width; x++)
            column[x] = x * Width / frame.Width;

        var src = frame.Pixels;
        for (var y = 0; y < frame.Height; y++)
        {
            var rowBase = y * Height / frame.Height * Width;
            var offset = y * frame.Width * 4;
            for (var x = 0; x < frame.Width; x++, offset += 4)
            {
                // BT.601 luma, with integer weights that sum to 256: white maps to exactly 255.
                var luma = (src[offset] * 29 + src[offset + 1] * 150 + src[offset + 2] * 77) >> 8;
                var target = rowBase + column[x];
                sums[target] += luma;
                counts[target]++;
            }
        }

        var result = new byte[Width * Height];
        for (var i = 0; i < result.Length; i++)
            result[i] = (byte)((sums[i] + counts[i] / 2) / counts[i]);
        return result;
    }

    public static double MeanAbsoluteDifference(ReadOnlySpan<byte> a, ReadOnlySpan<byte> b)
    {
        if (a.Length != b.Length || a.Length == 0)
            throw new ArgumentException("Thumbnails must be non-empty and the same size.");

        long sum = 0;
        for (var i = 0; i < a.Length; i++)
            sum += Math.Abs(a[i] - b[i]);
        return (double)sum / a.Length;
    }
}

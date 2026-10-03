namespace CsharpCollector.Capture;

/// <summary>
/// In-memory image: 32-bit BGRA, top-down rows, stride = Width * 4. Alpha is undefined for screen
/// captures. Frames are never written to disk.
/// </summary>
internal sealed class Frame
{
    public Frame(int width, int height, byte[] pixels, DateTimeOffset capturedAt)
    {
        if (width <= 0 || height <= 0)
            throw new ArgumentOutOfRangeException(nameof(width), "Frame dimensions must be positive.");
        if (pixels.Length != width * height * 4)
            throw new ArgumentException($"Expected {width * height * 4} bytes, got {pixels.Length}.", nameof(pixels));

        Width = width;
        Height = height;
        Pixels = pixels;
        CapturedAt = capturedAt;
    }

    public int Width { get; }
    public int Height { get; }
    public byte[] Pixels { get; }
    public DateTimeOffset CapturedAt { get; }

    public Frame Clone() => new(Width, Height, (byte[])Pixels.Clone(), CapturedAt);
}

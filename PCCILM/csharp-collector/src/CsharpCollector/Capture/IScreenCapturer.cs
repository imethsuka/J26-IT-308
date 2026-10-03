namespace CsharpCollector.Capture;

internal interface IScreenCapturer
{
    /// <summary>
    /// Full-resolution primary-screen capture, or null if the screen can't be read. The frame's
    /// pixel memory is reused by the next call, so <see cref="Frame.Clone"/> it to keep it.
    /// </summary>
    Frame? Capture();
}

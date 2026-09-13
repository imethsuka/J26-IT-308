using CsharpCollector.Capture;
using CsharpCollector.Events;
using CsharpCollector.Sensing;

namespace CsharpCollector.Tests;

/// <summary>Stands in for the GDI capturer. It returns whatever synthetic "screen" the test sets.</summary>
internal sealed class FakeCapturer : IScreenCapturer
{
    public Frame? Screen { get; set; }
    public int Calls { get; private set; }
    public Action? DuringCapture { get; set; }

    public Frame? Capture()
    {
        Calls++;
        DuringCapture?.Invoke();
        return Screen;
    }
}

public class ScreenChangeMonitorTests
{
    private readonly FakeCapturer _capturer = new() { Screen = SyntheticFrames.Grey(640, 360, 0) };
    private readonly ContextGate _gate = new();
    private readonly RecordingSink _sink = new();
    private readonly FrameBuffer _buffer = new();
    private readonly ScreenChangeMonitor _monitor;

    public ScreenChangeMonitorTests()
    {
        _monitor = new ScreenChangeMonitor(_capturer, new ScreenChangeDetector(3), _buffer, _gate, _sink, _ => { });
    }

    private void AllowContext() => _gate.MarkAllowed(_gate.NextSignal());

    [Fact]
    public void Nothing_is_captured_before_the_context_is_evaluated()
    {
        Assert.Equal(ProbeOutcome.Excluded, _monitor.ProbeOnce());
        Assert.Equal(0, _capturer.Calls);
    }

    [Fact]
    public void Excluded_context_is_never_captured()
    {
        AllowContext();
        _gate.NextSignal(); // switched to a window that was then excluded, so it's never marked allowed

        Assert.Equal(ProbeOutcome.Excluded, _monitor.ProbeOnce());
        Assert.Equal(0, _capturer.Calls);
    }

    [Fact]
    public void First_probe_stores_the_frame_and_emits_event()
    {
        AllowContext();

        Assert.Equal(ProbeOutcome.Captured, _monitor.ProbeOnce());
        Assert.Equal(_capturer.Screen!.Pixels, _buffer.PeekLatest()!.Pixels);
        var evt = Assert.IsType<FrameCapturedEvent>(Assert.Single(_sink.Events));
        Assert.Equal(640, evt.Width);
        Assert.Null(evt.Diff);
    }

    [Fact]
    public void Stored_frame_is_a_copy_so_capturer_buffer_reuse_cannot_corrupt_it()
    {
        AllowContext();
        _monitor.ProbeOnce();

        Array.Fill(_capturer.Screen!.Pixels, (byte)255);

        Assert.NotSame(_capturer.Screen, _buffer.PeekLatest());
        Assert.All(_buffer.PeekLatest()!.Pixels.Where((_, i) => i % 4 != 3), p => Assert.Equal(0, p));
    }

    [Fact]
    public void Unchanged_screen_stores_nothing_new()
    {
        AllowContext();
        _monitor.ProbeOnce();
        var stored = _buffer.PeekLatest();

        Assert.Equal(ProbeOutcome.Unchanged, _monitor.ProbeOnce());
        Assert.Same(stored, _buffer.PeekLatest());
        Assert.Single(_sink.Events);
    }

    [Fact]
    public void Changed_screen_replaces_the_single_buffered_frame()
    {
        AllowContext();
        _monitor.ProbeOnce();
        _capturer.Screen = SyntheticFrames.WithRect(_capturer.Screen!, 0, 0, 320, 360, 255);

        Assert.Equal(ProbeOutcome.Captured, _monitor.ProbeOnce());
        Assert.Equal(1, _buffer.Count);
        Assert.Equal(_capturer.Screen.Pixels, _buffer.PeekLatest()!.Pixels);
        Assert.Equal(127.5, Assert.IsType<FrameCapturedEvent>(_sink.Events[^1]).Diff);
    }

    [Fact]
    public void Larger_buffer_keeps_recent_triggered_frames()
    {
        var buffer = new FrameBuffer(capacity: 2);
        var monitor = new ScreenChangeMonitor(_capturer, new ScreenChangeDetector(3), buffer, _gate, _sink, _ => { });
        AllowContext();

        foreach (byte level in new byte[] { 0, 100, 200 })
        {
            _capturer.Screen = SyntheticFrames.Grey(640, 360, level);
            monitor.ProbeOnce();
        }

        Assert.Equal(new byte[] { 100, 200 }, buffer.Snapshot().Select(f => f.Pixels[0]));
    }

    [Fact]
    public void Window_switch_during_capture_drops_the_frame_and_keeps_the_old_baseline()
    {
        AllowContext();
        _capturer.DuringCapture = () => _gate.NextSignal();

        Assert.Equal(ProbeOutcome.Excluded, _monitor.ProbeOnce());
        Assert.Equal(0, _buffer.Count);
        Assert.Empty(_sink.Events);

        // Once the new context is allowed, the same screen still counts as new: no baseline was accepted.
        _capturer.DuringCapture = null;
        AllowContext();
        Assert.Equal(ProbeOutcome.Captured, _monitor.ProbeOnce());
    }

    [Fact]
    public void Failed_capture_is_reported()
    {
        AllowContext();
        _capturer.Screen = null;

        Assert.Equal(ProbeOutcome.CaptureFailed, _monitor.ProbeOnce());
        Assert.Equal(0, _buffer.Count);
    }
}

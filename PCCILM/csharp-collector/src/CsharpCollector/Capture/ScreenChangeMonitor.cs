using CsharpCollector.Events;
using CsharpCollector.Output;
using CsharpCollector.Sensing;

namespace CsharpCollector.Capture;

internal enum ProbeOutcome
{
    /// <summary>The foreground context is excluded, or became excluded mid-probe. Nothing is kept.</summary>
    Excluded,
    CaptureFailed,
    Unchanged,
    Captured,
}

/// <summary>
/// Each probe: exclusion check -> screen grab -> 64x36 greyscale thumbnail -> diff against
/// baseline -> (on change) exclusion re-check -> copy that same frame into <see cref="FrameBuffer"/>.
/// </summary>
internal sealed class ScreenChangeMonitor
{
    private readonly IScreenCapturer _capturer;
    private readonly ScreenChangeDetector _detector;
    private readonly ContextGate _gate;
    private readonly IEventSink _sink;
    private readonly Action<string> _log;
    private readonly Action<Frame, double?>? _onCaptured;

    public ScreenChangeMonitor(
        IScreenCapturer capturer, ScreenChangeDetector detector, FrameBuffer buffer,
        ContextGate gate, IEventSink sink, Action<string> log, Action<Frame, double?>? onCaptured = null)
    {
        _capturer = capturer;
        _detector = detector;
        Buffer = buffer;
        _gate = gate;
        _sink = sink;
        _log = log;
        _onCaptured = onCaptured;
    }

    public FrameBuffer Buffer { get; }

    public ProbeOutcome ProbeOnce()
    {
        if (_gate.TryGetAllowedToken() is not { } token)
            return ProbeOutcome.Excluded;

        var grab = _capturer.Capture();
        if (grab is null)
            return ProbeOutcome.CaptureFailed;

        var thumbnail = Thumbnail.FromFrame(grab);
        var decision = _detector.Evaluate(thumbnail);
        if (!decision.Changed)
            return ProbeOutcome.Unchanged;

        // The foreground may have switched to an excluded app while we were capturing. If so,
        // this frame may show it, so drop the frame without storing it or updating the baseline.
        if (!_gate.IsStillAllowed(token))
            return ProbeOutcome.Excluded;

        var frame = grab.Clone();
        Buffer.Add(frame);
        _detector.Accept(thumbnail);
        _sink.Emit(new FrameCapturedEvent(
            frame.CapturedAt, frame.Width, frame.Height,
            decision.Difference is { } d ? Math.Round(d, 2) : null));
        _onCaptured?.Invoke(frame, decision.Difference);
        return ProbeOutcome.Captured;
    }

    public async Task RunAsync(TimeSpan interval, CancellationToken cancellationToken)
    {
        using var timer = new PeriodicTimer(interval);
        try
        {
            while (await timer.WaitForNextTickAsync(cancellationToken))
            {
                try
                {
                    ProbeOnce();
                }
                catch (Exception ex)
                {
                    _log($"screen probe failed: {ex.Message}");
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
    }
}

using CsharpCollector.Native;
using CsharpCollector.Sensing;

namespace CsharpCollector.Content;

/// <summary>Reads the screen content that goes with a frame that is about to be published.</summary>
internal interface IScreenTextSource
{
    ScreenContent? TryRead();
}

/// <summary>Reads one window's content. <see cref="UiaScreenContentReader"/> is the real one.</summary>
internal interface IScreenContentReader
{
    ScreenContent? TryRead(IntPtr hwnd);
}

/// <summary>
/// Reads the foreground window's text only while the context is evaluated and allowed, and throws the
/// result away if the foreground changed during the (slow) read, so the text always belongs to the
/// context in the same snapshot. It fails closed: no context, no read.
/// </summary>
internal sealed class ForegroundScreenTextSource : IScreenTextSource
{
    private readonly ContextGate _gate;
    private readonly CollectorState _state;
    private readonly IScreenContentReader _reader;
    private readonly Func<IntPtr> _foreground;
    private readonly Func<DateTimeOffset> _now;

    public ForegroundScreenTextSource(
        ContextGate gate, CollectorState state, IScreenContentReader reader,
        Func<IntPtr>? foreground = null, Func<DateTimeOffset>? now = null)
    {
        _gate = gate;
        _state = state;
        _reader = reader;
        _foreground = foreground ?? NativeMethods.GetForegroundWindow;
        _now = now ?? (() => DateTimeOffset.UtcNow);
    }

    public ScreenContent? TryRead()
    {
        if (_gate.TryGetAllowedToken() is not { } token)
            return null;

        var (context, _, _, _) = _state.Snapshot(_now());
        if (context.Excluded || string.IsNullOrEmpty(context.App))
            return null;

        var hwnd = _foreground();
        if (hwnd == IntPtr.Zero)
            return null;

        var content = _reader.TryRead(hwnd);
        return content is not null && _gate.IsStillAllowed(token) ? content : null;
    }
}

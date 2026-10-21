namespace CsharpCollector.Sensing;

/// <summary>
/// Decides whether sensing that depends on the foreground context (input counting, screen
/// capture) may run right now. Every window signal takes a new sequence number, and the context
/// is allowed only once the latest signal has been evaluated and allowed. Between a window
/// switch and its evaluation, everything is blocked, so an excluded app is never sensed, even briefly.
/// </summary>
internal sealed class ContextGate
{
    private long _signalSeq;
    private long _allowedSeq = -1;

    public long NextSignal() => Interlocked.Increment(ref _signalSeq);

    public void MarkAllowed(long sequence) => Interlocked.Exchange(ref _allowedSeq, sequence);

    public bool IsContextAllowed => Volatile.Read(ref _allowedSeq) == Volatile.Read(ref _signalSeq);

    /// <summary>
    /// Token for a multi-step operation (e.g. a capture). Check it with <see cref="IsStillAllowed"/>
    /// afterwards to confirm no window change happened in between.
    /// </summary>
    public long? TryGetAllowedToken()
    {
        var allowed = Volatile.Read(ref _allowedSeq);
        return allowed == Volatile.Read(ref _signalSeq) ? allowed : null;
    }

    public bool IsStillAllowed(long token) =>
        Volatile.Read(ref _allowedSeq) == token && Volatile.Read(ref _signalSeq) == token;
}

using System.Runtime.InteropServices;
using CsharpCollector.Events;
using CsharpCollector.Native;
using CsharpCollector.Output;

namespace CsharpCollector.Sensing;

/// <summary>Polls GetLastInputInfo and emits idle_start / idle_end when the threshold is crossed.</summary>
internal sealed class IdleMonitor
{
    private readonly TimeSpan _threshold;
    private readonly IEventSink _sink;
    private bool _isIdle;
    private DateTimeOffset _idleSince;

    public IdleMonitor(TimeSpan threshold, IEventSink sink)
    {
        _threshold = threshold;
        _sink = sink;
    }

    public void Poll()
    {
        var now = DateTimeOffset.UtcNow;
        var idleFor = GetTimeSinceLastInput();
        var lastInput = now - idleFor;

        if (!_isIdle && idleFor >= _threshold)
        {
            _isIdle = true;
            _idleSince = lastInput;
            _sink.Emit(new IdleStartEvent(now, _idleSince));
        }
        else if (_isIdle && idleFor < _threshold)
        {
            _isIdle = false;
            _sink.Emit(new IdleEndEvent(now, Math.Round((lastInput - _idleSince).TotalSeconds, 1)));
        }
    }

    private static TimeSpan GetTimeSinceLastInput()
    {
        var info = new NativeMethods.LASTINPUTINFO { cbSize = (uint)Marshal.SizeOf<NativeMethods.LASTINPUTINFO>() };
        if (!NativeMethods.GetLastInputInfo(ref info))
            return TimeSpan.Zero;

        // Both values are 32-bit tick counts that wrap after about 49.7 days. Unsigned
        // subtraction still gives the right difference across a wrap.
        var elapsedMs = unchecked((uint)Environment.TickCount - info.dwTime);
        return TimeSpan.FromMilliseconds(elapsedMs);
    }
}

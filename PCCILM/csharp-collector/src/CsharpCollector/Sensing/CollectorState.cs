using CsharpCollector.Events;
using CsharpCollector.Filtering;
using CsharpCollector.Output;

namespace CsharpCollector.Sensing;

internal readonly record struct ContextState(bool Excluded, string? App, string? Title, string? Domain, DateTimeOffset Since);

internal readonly record struct IdleState(bool IsIdle, DateTimeOffset? IdleSince);

/// <summary>
/// Latest sensed state, kept up to date by observing the event stream. It touches none of the
/// sensing classes: it is wired in as an <see cref="IEventSink"/> alongside the real sink, so the
/// IPC snapshot is assembled from exactly the events that were emitted. Updated on the
/// window-context and ticker threads, read on the capture thread, so access is locked.
/// </summary>
internal sealed class CollectorState : IEventSink
{
    private readonly object _lock = new();
    private ContextState _context = new(Excluded: true, null, null, null, DateTimeOffset.MinValue);
    private ActivityEvent? _activity;
    private IdleState _idle = new(false, null);

    public void Emit(CollectorEvent evt)
    {
        lock (_lock)
        {
            switch (evt)
            {
                case WindowEvent w:
                    var domain = ExclusionFilter.TryGetHost(w.Url);
                    // Dwell is time in the current app+title+domain, so only a real change resets it.
                    if (_context.Excluded || _context.App != w.Process || _context.Title != w.Title || _context.Domain != domain)
                        _context = new ContextState(false, w.Process, w.Title, domain, w.Ts);
                    break;
                case ExcludedContextEvent x:
                    _context = new ContextState(true, null, null, null, x.Ts);
                    break;
                case ActivityEvent a:
                    _activity = a;
                    break;
                case IdleStartEvent i:
                    _idle = new IdleState(true, i.IdleSince);
                    break;
                case IdleEndEvent:
                    _idle = new IdleState(false, null);
                    break;
            }
        }
    }

    public (ContextState Context, ActivityEvent? Activity, IdleState Idle, DateTimeOffset Now) Snapshot(DateTimeOffset now)
    {
        lock (_lock)
            return (_context, _activity, _idle, now);
    }
}

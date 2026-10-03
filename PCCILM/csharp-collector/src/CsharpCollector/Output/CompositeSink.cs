using CsharpCollector.Events;

namespace CsharpCollector.Output;

/// <summary>Fans one event out to several sinks, in order.</summary>
internal sealed class CompositeSink : IEventSink
{
    private readonly IEventSink[] _sinks;

    public CompositeSink(params IEventSink[] sinks) => _sinks = sinks;

    public void Emit(CollectorEvent evt)
    {
        foreach (var sink in _sinks)
            sink.Emit(evt);
    }
}

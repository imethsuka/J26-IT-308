using CsharpCollector.Events;

namespace CsharpCollector.Output;

internal interface IEventSink
{
    void Emit(CollectorEvent evt);
}

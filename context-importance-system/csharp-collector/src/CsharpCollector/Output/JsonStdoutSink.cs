using System.Text.Json;
using System.Text.Json.Serialization;
using CsharpCollector.Events;

namespace CsharpCollector.Output;

/// <summary>
/// Writes one JSON object per line (JSON Lines) to stdout. Diagnostics go to stderr so that
/// stdout stays machine-readable until IPC replaces this sink.
/// </summary>
internal sealed class JsonStdoutSink : IEventSink
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly object _lock = new();

    public void Emit(CollectorEvent evt)
    {
        var line = JsonSerializer.Serialize(evt, evt.GetType(), Options);
        lock (_lock)
        {
            Console.Out.WriteLine(line);
        }
    }
}

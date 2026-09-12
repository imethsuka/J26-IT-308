using System.Text.Json.Serialization;

namespace CsharpCollector.Events;

internal abstract record CollectorEvent(
    [property: JsonPropertyOrder(-2)] string Type,
    [property: JsonPropertyOrder(-1)] DateTimeOffset Ts);

/// <summary>Foreground switch ("foreground") or caption change ("title_change") in an allowed context.</summary>
internal sealed record WindowEvent(string Type, DateTimeOffset Ts, string Process, string Title, string? Url)
    : CollectorEvent(Type, Ts);

/// <summary>
/// Marker for entering an excluded context. It deliberately carries no app, title or URL, so
/// downstream code knows the previous context ended without learning what replaced it.
/// </summary>
internal sealed record ExcludedContextEvent(DateTimeOffset Ts) : CollectorEvent("excluded_context", Ts);

/// <summary>Input counts over the last <see cref="WindowSeconds"/> seconds. Counts only, never content.</summary>
internal sealed record ActivityEvent(
    DateTimeOffset Ts, int WindowSeconds, int KeyPresses, int MouseClicks, int MouseScrolls, int MouseMoves)
    : CollectorEvent("activity", Ts);

/// <summary>A frame was stored in the in-memory buffer. <see cref="Diff"/> is null for the first frame.</summary>
internal sealed record FrameCapturedEvent(DateTimeOffset Ts, int Width, int Height, double? Diff)
    : CollectorEvent("frame_captured", Ts);

internal sealed record IdleStartEvent(DateTimeOffset Ts, DateTimeOffset IdleSince) : CollectorEvent("idle_start", Ts);

internal sealed record IdleEndEvent(DateTimeOffset Ts, double IdleSeconds) : CollectorEvent("idle_end", Ts);

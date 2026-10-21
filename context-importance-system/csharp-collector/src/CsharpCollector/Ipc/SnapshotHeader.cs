using System.Globalization;

namespace CsharpCollector.Ipc;

// JSON header of a "context_snapshot" message (schema version 2: version 1 plus the optional "text" object). Serialized in snake_case.
// The protocol is documented in the README and in python-worker/sensing/protocol.py; keep all three in sync.

internal sealed record SnapshotHeader(
    string Schema,
    int Version,
    long Seq,
    string Ts,
    string Trigger,
    SnapshotContext? Context,
    SnapshotActivity? Activity,
    SnapshotIdle Idle,
    SnapshotFrame? Frame,
    SnapshotText? Text = null)
{
    public const string SchemaName = "context_snapshot";
    public const int SchemaVersion = 2;

    // Six fractional digits and a 'Z' suffix, which Python 3.11's datetime.fromisoformat parses exactly.
    public static string FormatTs(DateTimeOffset ts) =>
        ts.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", CultureInfo.InvariantCulture);
}

/// <summary>Excluded contexts carry no app, title or domain.</summary>
internal sealed record SnapshotContext(bool Excluded, string? App, string? Title, string? Domain, double DwellSeconds);

internal sealed record SnapshotActivity(int WindowSeconds, int KeyPresses, int MouseClicks, int MouseScrolls, int MouseMoves);

internal sealed record SnapshotIdle(bool IsIdle, double IdleSeconds);

/// <summary>Describes the raw pixel payload that follows the header.</summary>
internal sealed record SnapshotFrame(int Width, int Height, int Stride, string Format, string CapturedAt, double? Diff)
{
    public const string Bgra32 = "bgra32";
}

/// <summary>
/// The visible text and large images of the foreground window at the time of the frame. Absent (null)
/// when the feature is off, the context is excluded, or nothing could be read.
/// </summary>
internal sealed record SnapshotText(
    string Source, int Chars, bool Truncated, double ReadMs, string Content, IReadOnlyList<SnapshotImage> Images);

/// <summary>A large on-screen picture: bounds in screen pixels and its alt text, if any.</summary>
internal sealed record SnapshotImage(int X, int Y, int Width, int Height, string? Alt);

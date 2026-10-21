using CsharpCollector.Capture;
using CsharpCollector.Content;
using CsharpCollector.Sensing;

namespace CsharpCollector.Ipc;

/// <summary>
/// Builds a "context_snapshot" from the current <see cref="CollectorState"/> plus a freshly
/// captured frame, and hands it to <see cref="WorkerConnection"/>. One snapshot is published per
/// captured frame, since the frame is the largest and least frequent part of the message.
/// </summary>
internal sealed class SnapshotPublisher
{
    private readonly CollectorState _state;
    private readonly WorkerConnection _connection;
    private readonly bool _includeFrameBytes;
    private readonly IScreenTextSource? _text;
    private long _seq;

    public SnapshotPublisher(CollectorState state, WorkerConnection connection, bool includeFrameBytes = true, IScreenTextSource? text = null)
    {
        _state = state;
        _connection = connection;
        _includeFrameBytes = includeFrameBytes;
        _text = text;
    }

    public void Publish(Frame frame, double? diff)
    {
        var header = Build(frame, diff, Interlocked.Increment(ref _seq), _text?.TryRead());
        _connection.Send(header, _includeFrameBytes ? frame.Pixels : ReadOnlyMemory<byte>.Empty);
    }

    internal SnapshotHeader Build(Frame frame, double? diff, long seq, ScreenContent? content = null)
    {
        var (context, activity, idle, now) = _state.Snapshot(frame.CapturedAt);

        var contextDto = context.Excluded
            ? new SnapshotContext(true, null, null, null, DwellSeconds(context.Since, now))
            : new SnapshotContext(false, context.App, context.Title, context.Domain, DwellSeconds(context.Since, now));

        var activityDto = activity is null
            ? null
            : new SnapshotActivity(activity.WindowSeconds, activity.KeyPresses, activity.MouseClicks, activity.MouseScrolls, activity.MouseMoves);

        var idleDto = new SnapshotIdle(idle.IsIdle, idle.IsIdle && idle.IdleSince is { } since ? RoundSeconds(now - since) : 0);

        var frameDto = new SnapshotFrame(
            frame.Width, frame.Height, frame.Width * 4, SnapshotFrame.Bgra32,
            SnapshotHeader.FormatTs(frame.CapturedAt),
            diff is { } d ? Math.Round(d, 2) : null);

        return new SnapshotHeader(
            SnapshotHeader.SchemaName, SnapshotHeader.SchemaVersion, seq,
            SnapshotHeader.FormatTs(now), Trigger: "frame",
            contextDto, activityDto, idleDto, _includeFrameBytes ? frameDto : frameDto with { Stride = 0 },
            content is null || context.Excluded ? null : ToWire(content));
    }

    private static SnapshotText ToWire(ScreenContent c) => new(
        c.Source, c.Text.Length, c.Truncated, Math.Round(c.ReadMs, 1), c.Text,
        c.Images.Select(i => new SnapshotImage(i.Bounds.X, i.Bounds.Y, i.Bounds.Width, i.Bounds.Height, i.AltText)).ToList());

    private static double DwellSeconds(DateTimeOffset since, DateTimeOffset now) =>
        since == DateTimeOffset.MinValue ? 0 : RoundSeconds(now - since);

    private static double RoundSeconds(TimeSpan span) => Math.Round(Math.Max(0, span.TotalSeconds), 1);
}

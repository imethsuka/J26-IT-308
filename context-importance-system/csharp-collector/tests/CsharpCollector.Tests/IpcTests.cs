using System.Buffers.Binary;
using System.Text;
using System.Text.Json;
using CsharpCollector.Capture;
using CsharpCollector.Events;
using CsharpCollector.Ipc;
using CsharpCollector.Sensing;

namespace CsharpCollector.Tests;

public class WireFormatTests
{
    [Fact]
    public void Frame_prefix_is_magic_then_two_little_endian_lengths()
    {
        var header = new SnapshotHeader(
            SnapshotHeader.SchemaName, SnapshotHeader.SchemaVersion, 1, "2026-09-24T00:00:00.000000Z",
            "frame", new SnapshotContext(false, "chrome", "Docs", "docs.example.com", 1.0),
            new SnapshotActivity(10, 1, 2, 3, 4), new SnapshotIdle(false, 0),
            new SnapshotFrame(2, 1, 8, SnapshotFrame.Bgra32, "2026-09-24T00:00:00.000000Z", 5.0));
        var headerBytes = WireFormat.SerializeHeader(header);
        var payload = new byte[8];

        using var ms = new MemoryStream();
        WireFormat.WriteAsync(ms, headerBytes, payload, default).GetAwaiter().GetResult();
        var wire = ms.ToArray();

        Assert.Equal("CIS1"u8.ToArray(), wire[..4]);
        Assert.Equal((uint)headerBytes.Length, BinaryPrimitives.ReadUInt32LittleEndian(wire.AsSpan(4)));
        Assert.Equal((uint)payload.Length, BinaryPrimitives.ReadUInt32LittleEndian(wire.AsSpan(8)));
        Assert.Equal(WireFormat.PrefixSize + headerBytes.Length + payload.Length, wire.Length);
    }

    [Fact]
    public void Header_is_serialized_as_snake_case_json()
    {
        var header = new SnapshotHeader(
            SnapshotHeader.SchemaName, 1, 7, "2026-09-24T00:00:00.000000Z", "frame",
            new SnapshotContext(false, "chrome", "Docs", "docs.example.com", 12.5),
            new SnapshotActivity(10, 42, 3, 7, 310), new SnapshotIdle(false, 0),
            new SnapshotFrame(1920, 1080, 7680, SnapshotFrame.Bgra32, "2026-09-24T00:00:00.000000Z", 6.98));

        using var doc = JsonDocument.Parse(Encoding.UTF8.GetString(WireFormat.SerializeHeader(header)));
        var root = doc.RootElement;

        Assert.Equal("context_snapshot", root.GetProperty("schema").GetString());
        Assert.Equal(7, root.GetProperty("seq").GetInt64());
        Assert.Equal("chrome", root.GetProperty("context").GetProperty("app").GetString());
        Assert.Equal(12.5, root.GetProperty("context").GetProperty("dwell_seconds").GetDouble());
        Assert.Equal(42, root.GetProperty("activity").GetProperty("key_presses").GetInt32());
        Assert.Equal(7680, root.GetProperty("frame").GetProperty("stride").GetInt32());
    }
}

public class SnapshotPublisherTests
{
    private static Frame FakeFrame() => SyntheticFrames.Grey(4, 2, 128);

    [Fact]
    public void Build_carries_context_activity_idle_and_frame_from_state()
    {
        var state = new CollectorState();
        state.Emit(new WindowEvent("foreground", DateTimeOffset.Parse("2026-09-24T00:00:00Z"), "chrome", "Docs", "https://docs.example.com/x"));
        state.Emit(new ActivityEvent(DateTimeOffset.Parse("2026-09-24T00:00:05Z"), 10, 42, 3, 7, 310));
        var publisher = new SnapshotPublisher(state, connection: null!, includeFrameBytes: true);
        var frame = FakeFrame();

        var header = publisher.Build(frame, diff: 6.98, seq: 3);

        Assert.Equal("context_snapshot", header.Schema);
        Assert.Equal(3, header.Seq);
        Assert.False(header.Context!.Excluded);
        Assert.Equal("chrome", header.Context.App);
        Assert.Equal("docs.example.com", header.Context.Domain);
        Assert.Equal(42, header.Activity!.KeyPresses);
        Assert.False(header.Idle.IsIdle);
        Assert.Equal(4, header.Frame!.Width);
        Assert.Equal(16, header.Frame.Stride); // 4px * 4 bytes
        Assert.Equal(6.98, header.Frame.Diff);
    }

    [Fact]
    public void Excluded_context_carries_no_app_title_or_domain()
    {
        var state = new CollectorState();
        state.Emit(new ExcludedContextEvent(DateTimeOffset.Parse("2026-09-24T00:00:00Z")));
        var publisher = new SnapshotPublisher(state, connection: null!, includeFrameBytes: true);

        var header = publisher.Build(FakeFrame(), diff: null, seq: 1);

        Assert.True(header.Context!.Excluded);
        Assert.Null(header.Context.App);
        Assert.Null(header.Context.Title);
        Assert.Null(header.Context.Domain);
    }

    [Fact]
    public void Idle_snapshot_reports_seconds_since_idle_start()
    {
        var state = new CollectorState();
        state.Emit(new WindowEvent("foreground", DateTimeOffset.Parse("2026-09-24T00:00:00Z"), "code", "main.cs", null));
        state.Emit(new IdleStartEvent(DateTimeOffset.Parse("2026-09-24T00:01:00Z"), DateTimeOffset.Parse("2026-09-24T00:00:00Z")));
        var publisher = new SnapshotPublisher(state, connection: null!, includeFrameBytes: false);

        // Frame captured 90s after idle began.
        var frame = new Frame(4, 2, new byte[32], DateTimeOffset.Parse("2026-09-24T00:01:30Z"));
        var header = publisher.Build(frame, diff: null, seq: 1);

        Assert.True(header.Idle.IsIdle);
        Assert.Equal(90, header.Idle.IdleSeconds);
    }

    [Fact]
    public void Metadata_only_mode_zeroes_the_stride_so_the_worker_expects_no_payload()
    {
        var state = new CollectorState();
        state.Emit(new WindowEvent("foreground", DateTimeOffset.Parse("2026-09-24T00:00:00Z"), "code", "main.cs", null));
        var publisher = new SnapshotPublisher(state, connection: null!, includeFrameBytes: false);

        var header = publisher.Build(FakeFrame(), diff: 1.0, seq: 1);

        Assert.Equal(0, header.Frame!.Stride);
    }
}

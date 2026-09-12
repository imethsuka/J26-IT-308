using System.Buffers.Binary;
using System.IO;
using System.Text.Json;
using CsharpCollector.Output;

namespace CsharpCollector.Ipc;

/// <summary>
/// Message framing, all integers little-endian:
/// <code>
///   "CIS1" (4 bytes) | header_length (uint32) | payload_length (uint32) | header (UTF-8 JSON) | payload (raw frame bytes)
/// </code>
/// Sizes come first, so the receiver can reject an oversized message before reading it.
/// </summary>
internal static class WireFormat
{
    public const int PrefixSize = 12;
    public static ReadOnlySpan<byte> Magic => "CIS1"u8;

    public static byte[] SerializeHeader(SnapshotHeader header) =>
        JsonSerializer.SerializeToUtf8Bytes(header, JsonStdoutSink.Options);

    public static async Task WriteAsync(Stream stream, byte[] header, ReadOnlyMemory<byte> payload, CancellationToken cancellationToken)
    {
        var prefix = new byte[PrefixSize];
        Magic.CopyTo(prefix);
        BinaryPrimitives.WriteUInt32LittleEndian(prefix.AsSpan(4), (uint)header.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(prefix.AsSpan(8), (uint)payload.Length);

        await stream.WriteAsync(prefix, cancellationToken);
        await stream.WriteAsync(header, cancellationToken);
        if (!payload.IsEmpty)
            await stream.WriteAsync(payload, cancellationToken);
        await stream.FlushAsync(cancellationToken);
    }
}

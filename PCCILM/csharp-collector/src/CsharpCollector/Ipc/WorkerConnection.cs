using System.IO;
using System.IO.Pipes;
using System.Net;
using System.Net.Sockets;
using System.Threading.Channels;
using CsharpCollector.Capture;

namespace CsharpCollector.Ipc;

/// <summary>
/// Client side of the collector -> worker channel. The Python worker is the server; this class
/// connects, reconnects, and sends messages from a bounded queue on a background task, so sensing
/// never blocks on IPC.
///
/// Transport notes:
///  - Named pipe (default): opened with PipeOptions.CurrentUserOnly, so .NET restricts the pipe
///    to the current user account and won't connect to a pipe owned by anyone else. Preferred,
///    because it stays off the network stack entirely.
///  - TCP (fallback): binds the loopback address only, so it is not reachable from other hosts.
///    It does not authenticate the peer, so prefer the named pipe unless something (e.g. cross
///    from a container) needs a socket.
///
/// If the queue fills because the worker is slow or disconnected, the oldest snapshot is dropped
/// rather than stalling capture. Frame payloads are large, so the queue is deliberately shallow.
/// </summary>
internal sealed class WorkerConnection : IAsyncDisposable
{
    public enum TransportKind { NamedPipe, Tcp }

    private readonly TransportKind _transport;
    private readonly string _pipeName;
    private readonly int _tcpPort;
    private readonly Action<string> _log;
    private readonly Channel<OutboundMessage> _queue;
    private readonly CancellationTokenSource _cts = new();
    private Task _pump = Task.CompletedTask;

    public WorkerConnection(TransportKind transport, string pipeName, int tcpPort, Action<string> log, int queueCapacity = 4)
    {
        _transport = transport;
        _pipeName = pipeName;
        _tcpPort = tcpPort;
        _log = log;
        _queue = Channel.CreateBounded<OutboundMessage>(new BoundedChannelOptions(queueCapacity)
        {
            FullMode = BoundedChannelFullMode.DropOldest,
            SingleReader = true,
        });
    }

    public void Start() => _pump = Task.Run(() => PumpAsync(_cts.Token));

    /// <summary>Non-blocking. The header is serialized on the caller; the frame payload is sent as-is.</summary>
    public void Send(SnapshotHeader header, ReadOnlyMemory<byte> payload)
    {
        var message = new OutboundMessage(WireFormat.SerializeHeader(header), payload);
        // DropOldest means TryWrite only fails while completing; ignore that.
        _queue.Writer.TryWrite(message);
    }

    private async Task PumpAsync(CancellationToken cancellationToken)
    {
        Stream? stream = null;
        try
        {
            await foreach (var message in _queue.Reader.ReadAllAsync(cancellationToken))
            {
                try
                {
                    // Reuse the open connection across messages; only (re)connect when there isn't one.
                    stream ??= await ConnectAsync(cancellationToken);
                    await WireFormat.WriteAsync(stream, message.Header, message.Payload, cancellationToken);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex) when (ex is IOException or SocketException or TimeoutException or UnauthorizedAccessException)
                {
                    // Worker not up yet or the connection dropped. Drop the connection and this
                    // message, back off, and reconnect on the next one.
                    _log($"ipc send failed ({ex.GetType().Name}): {ex.Message}");
                    await DisposeStreamAsync(stream);
                    stream = null;
                    await DelayQuietly(TimeSpan.FromSeconds(1), cancellationToken);
                }
            }
        }
        finally
        {
            await DisposeStreamAsync(stream);
        }
    }

    private static async ValueTask DisposeStreamAsync(Stream? stream)
    {
        if (stream is not null)
            await stream.DisposeAsync();
    }

    private async Task<Stream> ConnectAsync(CancellationToken cancellationToken)
    {
        if (_transport == TransportKind.NamedPipe)
        {
            var pipe = new NamedPipeClientStream(".", _pipeName, PipeDirection.Out, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
            await pipe.ConnectAsync(2000, cancellationToken);
            return pipe;
        }

        var client = new TcpClient();
        await client.ConnectAsync(IPAddress.Loopback, _tcpPort, cancellationToken);
        return client.GetStream();
    }

    private static async Task DelayQuietly(TimeSpan delay, CancellationToken cancellationToken)
    {
        try
        {
            await Task.Delay(delay, cancellationToken);
        }
        catch (OperationCanceledException)
        {
        }
    }

    public async ValueTask DisposeAsync()
    {
        _queue.Writer.TryComplete();
        _cts.Cancel();
        try
        {
            await _pump;
        }
        catch (OperationCanceledException)
        {
        }
        _cts.Dispose();
    }

    private readonly record struct OutboundMessage(byte[] Header, ReadOnlyMemory<byte> Payload);
}

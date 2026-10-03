using System.Collections.Concurrent;
using System.IO;
using System.Text.Json;
using CsharpCollector.Browser;
using CsharpCollector.Capture;
using CsharpCollector.Config;
using CsharpCollector.Content;
using CsharpCollector.Events;
using CsharpCollector.Filtering;
using CsharpCollector.Hooks;
using CsharpCollector.Ipc;
using CsharpCollector.Native;
using CsharpCollector.Output;
using CsharpCollector.Sensing;

namespace CsharpCollector;

/// <summary>
/// Desktop sensing only, with events written to stdout as JSON Lines. Concurrent loops:
///   win32-hooks    : message loop receiving WinEvent + low-level hook callbacks (must never block)
///   window-context : exclusion check, then title/URL reads (UI Automation is slow)
///   screen probe   : thumbnail diff and, on change, a full frame into the in-memory buffer (thread pool)
///   main           : 1-second ticker for activity windows and idle polling
/// </summary>
internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        DpiAwareness.EnablePerMonitorV2();

        CollectorConfig config;
        try
        {
            config = CollectorConfig.Load(GetConfigPath(args));
        }
        catch (Exception ex) when (ex is IOException or InvalidDataException or JsonException)
        {
            Log($"config error: {ex.Message}");
            return 1;
        }

        var state = new CollectorState();
        // The state tracker observes every event alongside stdout, so the IPC snapshot is built
        // from exactly what was emitted.
        var sink = new CompositeSink(new JsonStdoutSink(), state);
        var filter = new ExclusionFilter(config.ExcludedApps, config.ExcludedDomains);
        var gate = new ContextGate();
        var counter = new ActivityCounter(config.ActivityWindowSeconds);
        var idle = new IdleMonitor(TimeSpan.FromSeconds(config.IdleThresholdSeconds), sink);
        var processor = new WindowContextProcessor(
            filter, new Win32WindowInfoSource(), new UiaBrowserUrlReader(), config.BrowserProcesses, sink);

        WorkerConnection? connection = null;
        SnapshotPublisher? publisher = null;
        if (config.Ipc.Enabled)
        {
            var transport = string.Equals(config.Ipc.Transport, "tcp", StringComparison.OrdinalIgnoreCase)
                ? WorkerConnection.TransportKind.Tcp
                : WorkerConnection.TransportKind.NamedPipe;
            connection = new WorkerConnection(transport, config.Ipc.PipeName, config.Ipc.TcpPort, Log);
            connection.Start();
            IScreenTextSource? textSource = null;
            if (CommandLine.ScreenText(args) ?? config.ScreenContent.Enabled)
            {
                textSource = new ForegroundScreenTextSource(gate, state, new UiaScreenContentReader(config.ScreenContent.ToLimits()));
                Log("screen text on: visible text and images are read with each captured frame");
            }
            publisher = new SnapshotPublisher(state, connection, config.Ipc.SendFrameBytes, textSource);
            Log(transport == WorkerConnection.TransportKind.NamedPipe
                ? $"ipc on: named pipe \\\\.\\pipe\\{config.Ipc.PipeName} (current user only)"
                : $"ipc on: tcp 127.0.0.1:{config.Ipc.TcpPort}");
        }

        using var signals = new BlockingCollection<WindowSignal>();

        // Runs on the hook thread. An exception escaping a native callback would kill the
        // process, so a signal that arrives during shutdown is just dropped.
        void Enqueue(WindowSignalKind kind, IntPtr hwnd)
        {
            try
            {
                signals.TryAdd(new WindowSignal(kind, hwnd, gate.NextSignal()));
            }
            catch (InvalidOperationException)
            {
            }
        }

        var processorThread = new Thread(() => ProcessSignals(signals, processor, gate))
        {
            IsBackground = true,
            Name = "window-context",
        };
        processorThread.SetApartmentState(ApartmentState.MTA);

        var windowHooks = new ForegroundWindowHooks(Enqueue);
        var inputHooks = new InputActivityHooks(counter, gate);
        var hookThread = new HookThread(
            install: () =>
            {
                windowHooks.Install();
                inputHooks.Install();
                var foreground = NativeMethods.GetForegroundWindow();
                if (foreground != IntPtr.Zero)
                    Enqueue(WindowSignalKind.Foreground, foreground);
            },
            uninstall: () =>
            {
                inputHooks.Uninstall();
                windowHooks.Uninstall();
            });

        using var cts = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) =>
        {
            e.Cancel = true;
            cts.Cancel();
        };

        processorThread.Start();
        try
        {
            hookThread.Start();
        }
        catch (InvalidOperationException ex)
        {
            Log($"{ex.Message} {ex.InnerException?.Message}");
            return 1;
        }
        Log("collector started, press Ctrl+C to stop");

        GdiScreenCapturer? capturer = null;
        var captureTask = Task.CompletedTask;
        if (config.ScreenCapture.Enabled)
        {
            var capture = config.ScreenCapture;
            capturer = new GdiScreenCapturer();
            var monitor = new ScreenChangeMonitor(
                capturer, new ScreenChangeDetector(capture.DiffThreshold), new FrameBuffer(capture.BufferCapacity),
                gate, sink, Log, onCaptured: publisher is null ? null : publisher.Publish);
            captureTask = monitor.RunAsync(TimeSpan.FromMilliseconds(capture.ProbeIntervalMs), cts.Token);
            var (width, height) = GdiScreenCapturer.PrimaryScreenSize();
            Log($"screen capture on: primary monitor {width}x{height}, probe every {capture.ProbeIntervalMs} ms, " +
                $"threshold {capture.DiffThreshold}, buffer {capture.BufferCapacity}");
        }

        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
        long ticks = 0;
        try
        {
            while (await timer.WaitForNextTickAsync(cts.Token))
            {
                counter.Rotate();
                idle.Poll();
                if (++ticks % config.ActivityEmitIntervalSeconds == 0)
                {
                    var (c, seconds) = counter.Sum();
                    sink.Emit(new ActivityEvent(
                        DateTimeOffset.UtcNow, seconds, c.KeyPresses, c.MouseClicks, c.MouseScrolls, c.MouseMoves));
                }
            }
        }
        catch (OperationCanceledException)
        {
        }

        await captureTask;
        capturer?.Dispose();
        if (connection is not null)
            await connection.DisposeAsync();

        // Unhook before completing the queue so no callback can race the shutdown.
        hookThread.Dispose();
        signals.CompleteAdding();
        processorThread.Join(TimeSpan.FromSeconds(2));
        Log("collector stopped");
        return 0;
    }

    // Consecutive signals are coalesced: only the newest window state matters, and this keeps
    // bursts of title changes from queueing a slow UI Automation read for each one.
    private static void ProcessSignals(
        BlockingCollection<WindowSignal> signals, WindowContextProcessor processor, ContextGate gate)
    {
        foreach (var first in signals.GetConsumingEnumerable())
        {
            var latest = first;
            var sawForeground = first.Kind == WindowSignalKind.Foreground;
            while (signals.TryTake(out var next))
            {
                latest = next;
                sawForeground |= next.Kind == WindowSignalKind.Foreground;
            }
            if (sawForeground)
                latest = latest with { Kind = WindowSignalKind.Foreground };

            try
            {
                if (processor.Process(latest))
                    gate.MarkAllowed(latest.Sequence);
            }
            catch (Exception ex)
            {
                Log($"window processing failed: {ex.Message}");
            }
        }
    }

    private static string GetConfigPath(string[] args)
    {
        var index = Array.IndexOf(args, "--config");
        return index >= 0 && index + 1 < args.Length
            ? args[index + 1]
            : Path.Combine(AppContext.BaseDirectory, "collector.config.json");
    }

    private static void Log(string message) => Console.Error.WriteLine($"[collector] {message}");
}

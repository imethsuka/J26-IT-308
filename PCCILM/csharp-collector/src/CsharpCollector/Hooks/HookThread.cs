using CsharpCollector.Native;

namespace CsharpCollector.Hooks;

/// <summary>
/// Dedicated thread with a Win32 message loop. Out-of-context WinEvent callbacks and low-level
/// hook callbacks are delivered through the message queue of the thread that installed them,
/// so both kinds of hook are installed here and this loop must never block.
/// </summary>
internal sealed class HookThread : IDisposable
{
    private readonly Action _install;
    private readonly Action _uninstall;
    private readonly ManualResetEventSlim _started = new();
    private Thread? _thread;
    private uint _threadId;
    private Exception? _startError;

    public HookThread(Action install, Action uninstall)
    {
        _install = install;
        _uninstall = uninstall;
    }

    public void Start()
    {
        _thread = new Thread(Run) { IsBackground = true, Name = "win32-hooks" };
        _thread.Start();
        _started.Wait();
        if (_startError is not null)
            throw new InvalidOperationException("Failed to install hooks.", _startError);
    }

    private void Run()
    {
        _threadId = NativeMethods.GetCurrentThreadId();
        // Forces creation of this thread's message queue so PostThreadMessage(WM_QUIT) can't be lost.
        NativeMethods.PeekMessage(out _, IntPtr.Zero, 0, 0, NativeMethods.PM_NOREMOVE);

        try
        {
            _install();
        }
        catch (Exception ex)
        {
            _startError = ex;
            _started.Set();
            return;
        }
        _started.Set();

        while (NativeMethods.GetMessage(out var msg, IntPtr.Zero, 0, 0) > 0)
        {
            NativeMethods.TranslateMessage(ref msg);
            NativeMethods.DispatchMessage(ref msg);
        }

        _uninstall();
    }

    public void Dispose()
    {
        if (_thread is null)
            return;
        NativeMethods.PostThreadMessage(_threadId, NativeMethods.WM_QUIT, IntPtr.Zero, IntPtr.Zero);
        _thread.Join(TimeSpan.FromSeconds(2));
        _started.Dispose();
    }
}

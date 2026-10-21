using CsharpCollector.Native;
using CsharpCollector.Sensing;

namespace CsharpCollector.Hooks;

/// <summary>
/// Foreground-window and title-change notifications via SetWinEventHook (out-of-context,
/// no elevation needed). Callbacks only raise a <see cref="WindowSignal"/>. They read nothing,
/// because the exclusion check must run before any window data is touched.
/// </summary>
internal sealed class ForegroundWindowHooks
{
    private const uint Flags = NativeMethods.WINEVENT_OUTOFCONTEXT | NativeMethods.WINEVENT_SKIPOWNPROCESS;

    private readonly Action<WindowSignalKind, IntPtr> _onSignal;
    // Kept in a field so the GC can't collect the delegate while native code still holds a pointer to it.
    private readonly NativeMethods.WinEventProc _callback;
    private IntPtr _foregroundHook;
    private IntPtr _nameChangeHook;

    public ForegroundWindowHooks(Action<WindowSignalKind, IntPtr> onSignal)
    {
        _onSignal = onSignal;
        _callback = OnWinEvent;
    }

    /// <summary>Must be called on the <see cref="HookThread"/>.</summary>
    public void Install()
    {
        _foregroundHook = NativeMethods.SetWinEventHook(
            NativeMethods.EVENT_SYSTEM_FOREGROUND, NativeMethods.EVENT_SYSTEM_FOREGROUND,
            IntPtr.Zero, _callback, 0, 0, Flags);
        if (_foregroundHook == IntPtr.Zero)
            throw new InvalidOperationException("SetWinEventHook(EVENT_SYSTEM_FOREGROUND) failed.");

        HookNameChangesFor(NativeMethods.GetForegroundWindow());
    }

    public void Uninstall()
    {
        if (_foregroundHook != IntPtr.Zero)
            NativeMethods.UnhookWinEvent(_foregroundHook);
        if (_nameChangeHook != IntPtr.Zero)
            NativeMethods.UnhookWinEvent(_nameChangeHook);
        _foregroundHook = _nameChangeHook = IntPtr.Zero;
    }

    // EVENT_OBJECT_NAMECHANGE fires for every accessible object in every process (clocks,
    // progress bars, ...). Scoping the hook to the foreground process keeps the volume manageable.
    private void HookNameChangesFor(IntPtr hwnd)
    {
        if (_nameChangeHook != IntPtr.Zero)
            NativeMethods.UnhookWinEvent(_nameChangeHook);
        _nameChangeHook = IntPtr.Zero;

        if (hwnd == IntPtr.Zero)
            return;
        NativeMethods.GetWindowThreadProcessId(hwnd, out var pid);
        if (pid == 0)
            return;

        _nameChangeHook = NativeMethods.SetWinEventHook(
            NativeMethods.EVENT_OBJECT_NAMECHANGE, NativeMethods.EVENT_OBJECT_NAMECHANGE,
            IntPtr.Zero, _callback, pid, 0, Flags);
    }

    private void OnWinEvent(IntPtr hook, uint eventType, IntPtr hwnd, int idObject, int idChild, uint thread, uint time)
    {
        if (hwnd == IntPtr.Zero)
            return;

        if (eventType == NativeMethods.EVENT_SYSTEM_FOREGROUND)
        {
            HookNameChangesFor(hwnd);
            _onSignal(WindowSignalKind.Foreground, hwnd);
            return;
        }

        // Only the caption of the foreground top-level window counts, not child controls.
        if (idObject != NativeMethods.OBJID_WINDOW || idChild != NativeMethods.CHILDID_SELF)
            return;
        if (hwnd != NativeMethods.GetForegroundWindow())
            return;

        _onSignal(WindowSignalKind.TitleChange, hwnd);
    }
}

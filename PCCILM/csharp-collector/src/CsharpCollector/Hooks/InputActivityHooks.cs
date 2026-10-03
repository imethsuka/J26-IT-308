using System.ComponentModel;
using System.Runtime.InteropServices;
using CsharpCollector.Native;
using CsharpCollector.Sensing;

namespace CsharpCollector.Hooks;

/// <summary>
/// Global low-level keyboard/mouse hooks that count activity. They never record content.
/// See Native/NativeMethods.cs for the antivirus false-positive risk these hooks carry.
/// </summary>
internal sealed class InputActivityHooks
{
    private readonly ActivityCounter _counter;
    private readonly ContextGate _gate;
    // Kept in fields so the GC can't collect the delegates while native code still holds pointers to them.
    private readonly NativeMethods.LowLevelHookProc _keyboardProc;
    private readonly NativeMethods.LowLevelHookProc _mouseProc;
    private IntPtr _keyboardHook;
    private IntPtr _mouseHook;

    public InputActivityHooks(ActivityCounter counter, ContextGate gate)
    {
        _counter = counter;
        _gate = gate;
        _keyboardProc = OnKeyboard;
        _mouseProc = OnMouse;
    }

    /// <summary>Must be called on the <see cref="HookThread"/>.</summary>
    public void Install()
    {
        var module = NativeMethods.GetModuleHandle(null);

        _keyboardHook = NativeMethods.SetWindowsHookEx(NativeMethods.WH_KEYBOARD_LL, _keyboardProc, module, 0);
        if (_keyboardHook == IntPtr.Zero)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "SetWindowsHookEx(WH_KEYBOARD_LL) failed.");

        _mouseHook = NativeMethods.SetWindowsHookEx(NativeMethods.WH_MOUSE_LL, _mouseProc, module, 0);
        if (_mouseHook == IntPtr.Zero)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "SetWindowsHookEx(WH_MOUSE_LL) failed.");
    }

    public void Uninstall()
    {
        if (_keyboardHook != IntPtr.Zero)
            NativeMethods.UnhookWindowsHookEx(_keyboardHook);
        if (_mouseHook != IntPtr.Zero)
            NativeMethods.UnhookWindowsHookEx(_mouseHook);
        _keyboardHook = _mouseHook = IntPtr.Zero;
    }

    // Only wParam (the message type) is inspected. lParam points at KBDLLHOOKSTRUCT, which holds
    // the key code, and is deliberately never dereferenced.
    private IntPtr OnKeyboard(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode == NativeMethods.HC_ACTION && _gate.IsContextAllowed)
        {
            var msg = (int)wParam;
            if (msg is NativeMethods.WM_KEYDOWN or NativeMethods.WM_SYSKEYDOWN)
                _counter.RecordKeyPress();
        }
        return NativeMethods.CallNextHookEx(_keyboardHook, nCode, wParam, lParam);
    }

    // Cursor coordinates in MSLLHOOKSTRUCT are not read either.
    private IntPtr OnMouse(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode == NativeMethods.HC_ACTION && _gate.IsContextAllowed)
        {
            switch ((int)wParam)
            {
                case NativeMethods.WM_MOUSEMOVE:
                    _counter.RecordMove();
                    break;
                case NativeMethods.WM_LBUTTONDOWN:
                case NativeMethods.WM_RBUTTONDOWN:
                case NativeMethods.WM_MBUTTONDOWN:
                case NativeMethods.WM_XBUTTONDOWN:
                    _counter.RecordClick();
                    break;
                case NativeMethods.WM_MOUSEWHEEL:
                case NativeMethods.WM_MOUSEHWHEEL:
                    _counter.RecordScroll();
                    break;
            }
        }
        return NativeMethods.CallNextHookEx(_mouseHook, nCode, wParam, lParam);
    }
}

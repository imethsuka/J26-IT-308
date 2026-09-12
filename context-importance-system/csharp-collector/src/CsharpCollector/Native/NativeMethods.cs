using System.Runtime.InteropServices;
using System.Text;

namespace CsharpCollector.Native;

// ============================================================================================
// Permission and antivirus risk notes for every OS API the collector uses
// ============================================================================================
//
// General: nothing here needs administrator rights. The collector must run inside the
// interactive user's session (a console app or per-user startup task, NOT a Session 0
// Windows Service): hooks, GetLastInputInfo and UI Automation only see the desktop of the
// session they run in.
//
// SetWindowsHookEx(WH_KEYBOARD_LL / WH_MOUSE_LL)  -> Hooks/InputActivityHooks.cs
//   Elevation: not required. Because of UIPI (User Interface Privilege Isolation), a
//     non-elevated hook does not receive input sent to an elevated window (e.g. an admin
//     terminal), so counts silently drop to zero while one has focus. Seeing that input
//     requires running the collector elevated, or uiAccess="true" in the app manifest
//     (the binary must be Authenticode-signed and installed under Program Files).
//   Antivirus/EDR: HIGH false-positive risk. A global WH_KEYBOARD_LL hook is the textbook
//     keylogger technique, and heuristic or behavioural engines flag unsigned binaries that
//     install one, whatever the callback does. Mitigations: code-sign the binary, submit it
//     to vendors for allow-listing, and keep the hook in this small auditable module.
//     The keyboard callback never reads KBDLLHOOKSTRUCT, so no key codes are ever seen.
//     WH_MOUSE_LL carries a lower but non-zero risk.
//   Reliability: if a callback exceeds LowLevelHooksTimeout (HKCU\Control Panel\Desktop),
//     Windows 7+ silently removes the hook. The callbacks therefore only bump counters.
//
// SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_OBJECT_NAMECHANGE), out-of-context
//   -> Hooks/ForegroundWindowHooks.cs
//   Elevation: not required. Antivirus: low risk; this is the accessibility event channel
//     that screen readers use.
//
// GetForegroundWindow / GetWindowText / GetWindowThreadProcessId  -> Sensing/Win32WindowInfoSource.cs
//   Elevation: not required. Antivirus: low risk on their own. Title polling combined with
//     input hooks is part of the "spyware" behavioural profile that some EDRs score as a whole.
//
// Process.GetProcessById(pid).ProcessName  -> Sensing/Win32WindowInfoSource.cs
//   Elevation: not required for the name, including elevated processes. Richer queries
//     (image path, modules) on elevated/protected processes fail with access denied; we
//     don't make them.
//
// GetLastInputInfo  -> Sensing/IdleMonitor.cs
//   Elevation: not required. Antivirus: benign. It is session-wide and tells us nothing about
//     which app received the input.
//
// UI Automation, reading the Edge/Chrome address bar  -> Browser/UiaBrowserUrlReader.cs
//   Elevation: not required for a browser at the same integrity level. UIPI blocks reading
//     an elevated browser (same uiAccess caveat as above).
//   Antivirus/EDR: MEDIUM risk. Reading another process's UI, and browser URLs in particular,
//     matches infostealer/browser-monitoring heuristics in some EDR products. It also makes
//     Chromium switch on its accessibility tree, which costs the browser some CPU and memory.
//
// GDI screen capture: GetDC(NULL) + BitBlt  -> Capture/GdiScreenCapturer.cs
//   Elevation: not required. It captures only the current interactive desktop. The secure
//     desktop (UAC prompt, lock screen, Ctrl+Alt+Del) can't be captured, and the blit fails.
//     Windows protected with SetWindowDisplayAffinity (some password managers, DRM video)
//     come out black.
//   Antivirus/EDR: MEDIUM risk on its own, HIGHER in combination. Periodic screenshots are a
//     classic spyware/infostealer signal, and EDR products score "screen capture + global
//     keyboard hook in the same unsigned process" as a stronger indicator than either alone.
//     Same mitigations as the hooks: code signing and vendor allow-listing.
//   Cost: under DWM, reading the desktop DC forces a GPU-to-CPU readback of the composed frame,
//     costing tens of ms whatever the output size (StretchBlt to 64x36 is no cheaper). Run the
//     benchmark project for this machine's numbers. DXGI Desktop Duplication is the faster
//     alternative if that budget is too high.
//
// SetProcessDpiAwarenessContext  -> Capture/DpiAwareness.cs
//   Elevation: not required. Antivirus: none. Without it, Windows would give this process
//     scaled ("virtualized") coordinates on high-DPI displays, and captures would cover only
//     part of the screen.
// ============================================================================================

internal static class NativeMethods
{
    public const uint EVENT_SYSTEM_FOREGROUND = 0x0003;
    public const uint EVENT_OBJECT_NAMECHANGE = 0x800C;
    public const uint WINEVENT_OUTOFCONTEXT = 0x0000;
    public const uint WINEVENT_SKIPOWNPROCESS = 0x0002;
    public const int OBJID_WINDOW = 0;
    public const int CHILDID_SELF = 0;

    public const int WH_KEYBOARD_LL = 13;
    public const int WH_MOUSE_LL = 14;
    public const int HC_ACTION = 0;

    public const int WM_QUIT = 0x0012;
    public const int WM_KEYDOWN = 0x0100;
    public const int WM_SYSKEYDOWN = 0x0104;
    public const int WM_MOUSEMOVE = 0x0200;
    public const int WM_LBUTTONDOWN = 0x0201;
    public const int WM_RBUTTONDOWN = 0x0204;
    public const int WM_MBUTTONDOWN = 0x0207;
    public const int WM_MOUSEWHEEL = 0x020A;
    public const int WM_XBUTTONDOWN = 0x020B;
    public const int WM_MOUSEHWHEEL = 0x020E;

    public const uint PM_NOREMOVE = 0x0000;

    public const int SM_CXSCREEN = 0;
    public const int SM_CYSCREEN = 1;
    public const uint SRCCOPY = 0x00CC0020;
    public const uint DIB_RGB_COLORS = 0;
    public const uint BI_RGB = 0;
    public static readonly IntPtr DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = new(-4);

    [StructLayout(LayoutKind.Sequential)]
    public struct BITMAPINFOHEADER
    {
        public uint biSize;
        public int biWidth;
        public int biHeight;
        public ushort biPlanes;
        public ushort biBitCount;
        public uint biCompression;
        public uint biSizeImage;
        public int biXPelsPerMeter;
        public int biYPelsPerMeter;
        public uint biClrUsed;
        public uint biClrImportant;
    }

    public delegate void WinEventProc(
        IntPtr hWinEventHook, uint eventType, IntPtr hwnd, int idObject, int idChild,
        uint idEventThread, uint dwmsEventTime);

    public delegate IntPtr LowLevelHookProc(int nCode, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    public struct LASTINPUTINFO
    {
        public uint cbSize;
        public uint dwTime;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct MSG
    {
        public IntPtr hwnd;
        public uint message;
        public IntPtr wParam;
        public IntPtr lParam;
        public uint time;
        public POINT pt;
    }

    [DllImport("user32.dll")]
    public static extern IntPtr SetWinEventHook(
        uint eventMin, uint eventMax, IntPtr hmodWinEventProc, WinEventProc lpfnWinEventProc,
        uint idProcess, uint idThread, uint dwFlags);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool UnhookWinEvent(IntPtr hWinEventHook);

    [DllImport("user32.dll", EntryPoint = "SetWindowsHookExW", SetLastError = true)]
    public static extern IntPtr SetWindowsHookEx(int idHook, LowLevelHookProc lpfn, IntPtr hMod, uint dwThreadId);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool UnhookWindowsHookEx(IntPtr hhk);

    [DllImport("user32.dll")]
    public static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

    [DllImport("kernel32.dll", EntryPoint = "GetModuleHandleW", CharSet = CharSet.Unicode)]
    public static extern IntPtr GetModuleHandle(string? lpModuleName);

    [DllImport("kernel32.dll")]
    public static extern uint GetCurrentThreadId();

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

    [DllImport("user32.dll", EntryPoint = "GetWindowTextLengthW")]
    public static extern int GetWindowTextLength(IntPtr hWnd);

    [DllImport("user32.dll", EntryPoint = "GetWindowTextW", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);

    [DllImport("user32.dll", EntryPoint = "GetMessageW")]
    public static extern int GetMessage(out MSG lpMsg, IntPtr hWnd, uint wMsgFilterMin, uint wMsgFilterMax);

    [DllImport("user32.dll", EntryPoint = "PeekMessageW")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool PeekMessage(out MSG lpMsg, IntPtr hWnd, uint wMsgFilterMin, uint wMsgFilterMax, uint wRemoveMsg);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool TranslateMessage(ref MSG lpMsg);

    [DllImport("user32.dll", EntryPoint = "DispatchMessageW")]
    public static extern IntPtr DispatchMessage(ref MSG lpMsg);

    [DllImport("user32.dll", EntryPoint = "PostThreadMessageW")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool PostThreadMessage(uint idThread, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int nIndex);

    [DllImport("user32.dll")]
    public static extern uint GetDpiForSystem();

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetProcessDpiAwarenessContext(IntPtr value);

    [DllImport("user32.dll")]
    public static extern IntPtr GetDC(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);

    [DllImport("gdi32.dll", SetLastError = true)]
    public static extern IntPtr CreateCompatibleDC(IntPtr hdc);

    [DllImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DeleteDC(IntPtr hdc);

    [DllImport("gdi32.dll", SetLastError = true)]
    public static extern IntPtr CreateDIBSection(
        IntPtr hdc, ref BITMAPINFOHEADER pbmi, uint usage, out IntPtr ppvBits, IntPtr hSection, uint offset);

    [DllImport("gdi32.dll")]
    public static extern IntPtr SelectObject(IntPtr hdc, IntPtr h);

    [DllImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DeleteObject(IntPtr ho);

    [DllImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool BitBlt(
        IntPtr hdcDest, int x, int y, int cx, int cy, IntPtr hdcSrc, int x1, int y1, uint rop);

    [DllImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GdiFlush();
}

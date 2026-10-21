using System.Diagnostics;
using System.Runtime.InteropServices;
using ContextDashboard.Services;
using ContextDashboard.Tray;
using ContextDashboard.Views;

namespace ContextDashboard;

/// <summary>
/// One exe, two modes, so that closing the window really gives its memory back:
/// <list type="bullet">
/// <item><c>--tray</c>: only a notification icon (WinForms, no WPF loaded). Always on, a few MB.</item>
/// <item>(default) / <c>--dashboard</c>: the WPF window. Closing it ends this process, so Windows reclaims all of it.</item>
/// </list>
/// The collector and the worker are separate processes that neither mode owns.
/// </summary>
public static class Program
{
    private const string TrayMutex = @"Local\ContextImportance.Tray";
    // CONTEXT_IMPORTANCE_INSTANCE lets a development build run beside an installed dashboard (same data, own window).
    private static readonly string DashboardMutex =
        @"Local\ContextImportance.Dashboard" + Environment.GetEnvironmentVariable("CONTEXT_IMPORTANCE_INSTANCE");

    [STAThread]
    public static int Main(string[] args)
    {
        var settings = AppSettings.Load();
        if (args.Contains("--tray"))
            return RunTray(settings, autostart: args.Contains("--autostart"));
        return RunDashboard(settings);
    }

    private static int RunTray(AppSettings settings, bool autostart)
    {
        using var mutex = new Mutex(true, TrayMutex, out var first);
        if (!first) return 0;

        System.Windows.Forms.Application.EnableVisualStyles();
        System.Windows.Forms.Application.SetHighDpiMode(System.Windows.Forms.HighDpiMode.PerMonitorV2);
        System.Windows.Forms.Application.Run(new TrayHost(settings, autostart));
        return 0;
    }

    private static int RunDashboard(AppSettings settings)
    {
        using var mutex = new Mutex(true, DashboardMutex, out var first);
        if (!first)
        {
            BringExistingToFront();
            return 0;
        }

        // The tray is what keeps the background services controllable once the window is closed.
        EnsureTrayRunning();

        var app = new System.Windows.Application { ShutdownMode = System.Windows.ShutdownMode.OnMainWindowClose };
        Suite.Core.SuiteTheme.Apply(app);

        // One window, two independent modules. Neither knows the other exists; the shell only sees IModule.
        using var privacy = new CAPRLMComparison.PrivacyModule();
        Suite.Core.IModule[] modules = [new MemoryModule(settings), privacy];
        return app.Run(new MainWindow(modules));
    }

    private static void EnsureTrayRunning()
    {
        if (Mutex.TryOpenExisting(TrayMutex, out var existing))
        {
            existing.Dispose();
            return;
        }
        if (Environment.ProcessPath is { } exe)
            Process.Start(new ProcessStartInfo(exe, "--tray") { UseShellExecute = false, CreateNoWindow = true })?.Dispose();
    }

    /// <summary>The tray's "Open dashboard" and a second launch both land here instead of opening a second window.</summary>
    public static void BringExistingToFront()
    {
        foreach (var process in Process.GetProcessesByName(Process.GetCurrentProcess().ProcessName))
        {
            using (process)
            {
                if (process.Id == Environment.ProcessId || process.MainWindowHandle == IntPtr.Zero) continue;
                ShowWindow(process.MainWindowHandle, 9 /* SW_RESTORE */);
                SetForegroundWindow(process.MainWindowHandle);
            }
        }
    }

    public static bool DashboardIsOpen()
    {
        if (!Mutex.TryOpenExisting(DashboardMutex, out var existing)) return false;
        existing.Dispose();
        return true;
    }

    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hWnd);
}

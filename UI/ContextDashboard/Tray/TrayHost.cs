using System.Diagnostics;
using System.Drawing;
using System.Runtime.InteropServices;
using ContextDashboard.Services;
using WinForms = System.Windows.Forms;

namespace ContextDashboard.Tray;

/// <summary>
/// The always-on part: a notification icon showing whether capture is running, with a menu to open the
/// dashboard and to start or stop the background services. No WPF is loaded in this process.
/// </summary>
public sealed class TrayHost : WinForms.ApplicationContext
{
    private readonly AppSettings _settings;
    private readonly ServiceControl _services;
    private readonly WinForms.NotifyIcon _icon;
    private readonly WinForms.Timer _timer;
    private readonly WinForms.ToolStripMenuItem _autostartItem;
    private Icon? _currentIcon;
    private ServiceState? _lastCollector, _lastWorker;
    private bool _busy;

    public TrayHost(AppSettings settings, bool autostart)
    {
        _settings = settings;
        _services = new ServiceControl(settings);

        var menu = new WinForms.ContextMenuStrip();
        menu.Items.Add("Open dashboard", null, (_, _) => OpenDashboard());
        menu.Items.Add(new WinForms.ToolStripSeparator());
        menu.Items.Add("Start capture", null, async (_, _) => await StartServicesAsync());
        menu.Items.Add("Stop capture", null, (_, _) => StopServices());
        menu.Items.Add(new WinForms.ToolStripSeparator());
        _autostartItem = new WinForms.ToolStripMenuItem("Start with Windows") { Checked = Autostart.IsEnabled(), CheckOnClick = true };
        _autostartItem.CheckedChanged += (_, _) => Autostart.Set(_autostartItem.Checked);
        menu.Items.Add(_autostartItem);
        menu.Items.Add("Exit tray (capture keeps running)", null, (_, _) => ExitThread());

        _icon = new WinForms.NotifyIcon { ContextMenuStrip = menu, Visible = true };
        _icon.DoubleClick += (_, _) => OpenDashboard();

        _timer = new WinForms.Timer { Interval = 5000 };
        _timer.Tick += (_, _) => { UpdateStatus(); TrimMemory(); };
        _timer.Start();
        UpdateStatus();
        TrimMemory();

        if (autostart) _ = StartServicesAsync();
    }

    private void UpdateStatus()
    {
        var collector = _services.Collector();
        var worker = _services.Worker();
        if (collector == _lastCollector && worker == _lastWorker) return;
        _lastCollector = collector;
        _lastWorker = worker;

        var both = collector == ServiceState.Running && worker == ServiceState.Running;
        var none = collector == ServiceState.Stopped && worker == ServiceState.Stopped;
        SetIcon(both ? Color.FromArgb(46, 160, 67) : none ? Color.Gray : Color.FromArgb(214, 158, 46));
        _icon.Text = both ? "Context capture: running"
            : none ? "Context capture: stopped"
            : $"Context capture: collector {collector.ToString().ToLowerInvariant()}, worker {worker.ToString().ToLowerInvariant()}";
    }

    private async Task StartServicesAsync()
    {
        if (_busy) return;
        _busy = true;
        try
        {
            _icon.Text = "Context capture: starting…";
            await _services.StartAsync();
        }
        catch (Exception ex)
        {
            _icon.ShowBalloonTip(5000, "Could not start capture", ex.Message, WinForms.ToolTipIcon.Warning);
        }
        finally
        {
            _busy = false;
            _lastCollector = _lastWorker = null;
            UpdateStatus();
        }
    }

    private void StopServices()
    {
        _services.Stop();
        _lastCollector = _lastWorker = null;
        UpdateStatus();
    }

    private static void OpenDashboard()
    {
        if (Program.DashboardIsOpen())
        {
            Program.BringExistingToFront();
            return;
        }
        if (Environment.ProcessPath is { } exe)
            Process.Start(new ProcessStartInfo(exe, "--dashboard") { UseShellExecute = false })?.Dispose();
    }

    /// <summary>A small coloured disc. The GDI handle is released as soon as the icon is cloned.</summary>
    private void SetIcon(Color color)
    {
        using var bitmap = new Bitmap(32, 32);
        using (var g = Graphics.FromImage(bitmap))
        {
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using var brush = new SolidBrush(color);
            g.FillEllipse(brush, 3, 3, 26, 26);
        }
        var handle = bitmap.GetHicon();
        try
        {
            var next = (Icon)Icon.FromHandle(handle).Clone();
            _icon.Icon = next;
            _currentIcon?.Dispose();
            _currentIcon = next;
        }
        finally
        {
            DestroyIcon(handle);
        }
    }

    /// <summary>
    /// The tray idles for hours: collect garbage and hand the pages the runtime no longer needs back to
    /// Windows, so it stays as small as it can between the five-second checks.
    /// </summary>
    private static void TrimMemory()
    {
        GC.Collect(2, GCCollectionMode.Forced, blocking: true, compacting: true);
        using var self = Process.GetCurrentProcess();
        EmptyWorkingSet(self.Handle);
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _timer.Dispose();
            _icon.Visible = false;
            _icon.Dispose();
            _currentIcon?.Dispose();
        }
        base.Dispose(disposing);
    }

    [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr handle);
    [DllImport("psapi.dll")] private static extern bool EmptyWorkingSet(IntPtr process);
}

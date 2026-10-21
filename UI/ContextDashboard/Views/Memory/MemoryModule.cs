using System.Windows;
using System.Windows.Threading;
using ContextDashboard.Services;
using Suite.Core;
using Wpf.Ui.Controls;

namespace ContextDashboard.Views;

/// <summary>
/// Member 1's slice of the app: the importance engine, the live feed, models, screen text. All pages share one
/// view-model, refreshed every 3 s only while the window is up (no background work in this process once it is closed).
/// </summary>
public sealed class MemoryModule : IModule
{
    private readonly MainViewModel _vm;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(3) };

    public MemoryModule(AppSettings settings)
    {
        _vm = new MainViewModel(settings);
        Backends = [new CaptureBackend(settings)];
        Pages =
        [
            Page("engine", "Importance engine", SymbolRegular.BrainCircuit24, () => new EnginePage()),
            Page("overview", "Overview", SymbolRegular.Home24, () => new OverviewPage()),
            Page("feed", "Live feed", SymbolRegular.DocumentBulletList24, () => new LiveFeedPage()),
            Page("models", "Models", SymbolRegular.Cube24, () => new ModelsPage()),
            Page("screen", "Screen text", SymbolRegular.TextT24, () => new ScreenTextPage()),
            Page("settings", "Settings", SymbolRegular.Settings24, () => new SettingsPage()),
        ];

        _timer.Tick += async (_, _) =>
        {
            // Nothing to show while minimised: skip the database reads entirely.
            if (System.Windows.Application.Current.MainWindow?.WindowState == WindowState.Minimized) return;
            await _vm.RefreshAsync();
            if (_vm.EngineVisible) await _vm.RefreshEngineAsync();
        };
        _timer.Start();
        _ = _vm.RefreshAsync();
    }

    public string Section => "MEMORY";
    public IReadOnlyList<ModulePage> Pages { get; }
    public IReadOnlyList<IBackend> Backends { get; }

    private ModulePage Page(string id, string title, SymbolRegular icon, Func<FrameworkElement> create) =>
        new(id, title, icon, () =>
        {
            var page = create();
            page.DataContext = _vm;
            return page;
        });
}

/// <summary>
/// The collector and the worker. They are separate processes that outlive the window by design (the tray controls them),
/// so this app starts them only when asked (<see cref="AppSettings.AutoStartCapture"/>) and never stops them on exit.
/// </summary>
internal sealed class CaptureBackend(AppSettings settings) : IBackend
{
    private readonly ServiceControl _services = new(settings);

    public string Name => "Capture engine";
    public bool StartWithApp => settings.AutoStartCapture;

    public Task<bool> IsRunningAsync() =>
        Task.FromResult(_services.Worker() == ServiceState.Running && _services.Collector() == ServiceState.Running);

    public async Task<string?> EnsureStartedAsync()
    {
        try
        {
            await _services.StartAsync();
            return null;
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException)
        {
            return ex.Message;
        }
    }

    public Task ShutdownAsync() => Task.CompletedTask;
}

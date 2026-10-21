using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;
using Suite.Core;
using Wpf.Ui.Controls;
using Brush = System.Windows.Media.Brush;
using Brushes = System.Windows.Media.Brushes;
using FontFamily = System.Windows.Media.FontFamily;
using Orientation = System.Windows.Controls.Orientation;

namespace ContextDashboard.Views;

/// <summary>
/// The shell: a sidebar built from the modules' sections and pages, a page host, and a footer showing which backends are up.
/// It starts each module's backend that asks to start with the app, and stops what it started when it closes.
/// </summary>
public partial class MainWindow : FluentWindow
{
    private readonly IReadOnlyList<IModule> _modules;
    private readonly List<ListBox> _lists = [];
    private readonly Dictionary<ModulePage, FrameworkElement> _created = [];
    private readonly List<BackendRow> _backends = [];
    private readonly DispatcherTimer _statusTimer = new() { Interval = TimeSpan.FromSeconds(4) };
    private bool _navigating;

    public MainWindow(IReadOnlyList<IModule> modules)
    {
        _modules = modules;
        InitializeComponent();
        BuildSidebar();
        BuildBackendRows();

        if (_lists.FirstOrDefault() is { } first) first.SelectedIndex = 0;

        _statusTimer.Tick += async (_, _) => await RefreshBackendsAsync();
        Loaded += async (_, _) =>
        {
            _statusTimer.Start();
            await StartBackendsAsync();
        };
        Closed += async (_, _) =>
        {
            _statusTimer.Stop();
            foreach (var backend in _modules.SelectMany(m => m.Backends)) await backend.ShutdownAsync();
        };
    }

    // ---- sidebar ---------------------------------------------------------------------------------------

    private void BuildSidebar()
    {
        var itemStyle = (Style)FindResource("NavItem");
        foreach (var module in _modules)
        {
            NavPanel.Children.Add(new TextBlock
            {
                Text = module.Section, FontFamily = new FontFamily("Consolas"), FontSize = 10,
                Foreground = (Brush)FindResource("Muted"), Margin = new Thickness(24, NavPanel.Children.Count == 0 ? 0 : 16, 0, 6),
            });

            var list = new ListBox { BorderThickness = new Thickness(0), Background = Brushes.Transparent, ItemContainerStyle = itemStyle };
            foreach (var page in module.Pages)
            {
                var row = new StackPanel { Orientation = Orientation.Horizontal };
                row.Children.Add(new SymbolIcon { Symbol = page.Icon, FontSize = 18 });
                row.Children.Add(new TextBlock { Text = page.Title, Margin = new Thickness(12, 0, 0, 0), FontWeight = FontWeights.SemiBold });
                list.Items.Add(new ListBoxItem { Content = row, Tag = page });
            }
            list.SelectionChanged += OnNavChanged;
            _lists.Add(list);
            NavPanel.Children.Add(list);
        }
    }

    private async void OnNavChanged(object sender, SelectionChangedEventArgs e)
    {
        // Several lists share one selection: picking in one clears the others. SelectionChanged also bubbles up
        // from controls inside the pages, so only react to the lists themselves.
        if (_navigating || !ReferenceEquals(e.OriginalSource, sender)) return;
        if (((ListBox)sender).SelectedItem is not ListBoxItem { Tag: ModulePage page }) return;

        _navigating = true;
        try
        {
            foreach (var other in _lists.Where(l => !ReferenceEquals(l, sender))) other.SelectedIndex = -1;
        }
        finally { _navigating = false; }

        var firstTime = !_created.TryGetValue(page, out var element);
        if (firstTime)
        {
            element = page.Create();
            _created[page] = element;
        }
        PageHost.Content = element;
        if (page.OnShown is not null) await page.OnShown(element!);
    }

    // ---- backends -----------------------------------------------------------------------------------------

    private sealed class BackendRow(IBackend backend, Ellipse dot, TextBlock text)
    {
        public IBackend Backend => backend;
        public Ellipse Dot => dot;
        public TextBlock Text => text;
        public bool Starting { get; set; }
        public string? Problem { get; set; }
    }

    private void BuildBackendRows()
    {
        foreach (var backend in _modules.SelectMany(m => m.Backends))
        {
            var dot = new Ellipse { Width = 8, Height = 8, Fill = (Brush)FindResource("Muted"), VerticalAlignment = VerticalAlignment.Center };
            var text = new TextBlock
            {
                FontFamily = new FontFamily("Consolas"), FontSize = 11, Foreground = (Brush)FindResource("Muted"),
                Margin = new Thickness(8, 0, 0, 0), Text = backend.Name.ToLowerInvariant() + ": checking…", TextTrimming = TextTrimming.CharacterEllipsis,
            };
            var line = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 2, 0, 2) };
            line.Children.Add(dot);
            line.Children.Add(text);
            BackendStatus.Children.Add(line);
            _backends.Add(new BackendRow(backend, dot, text));
        }
    }

    /// <summary>Starts every backend that asks to start with the app, each in the background, so the window opens at once.</summary>
    private Task StartBackendsAsync() => Task.WhenAll(_backends.Where(b => b.Backend.StartWithApp).Select(async row =>
    {
        row.Starting = true;
        await RefreshBackendsAsync();
        row.Problem = await Task.Run(row.Backend.EnsureStartedAsync);
        row.Starting = false;
        await RefreshBackendsAsync();
    }));

    private async Task RefreshBackendsAsync()
    {
        foreach (var row in _backends)
        {
            var running = await Task.Run(row.Backend.IsRunningAsync);
            var state = running ? "running" : row.Starting ? "starting…" : row.Problem is not null ? "failed" : "stopped";
            row.Text.Text = $"{row.Backend.Name.ToLowerInvariant()}: {state}";
            row.Text.ToolTip = row.Problem;
            row.Dot.Fill = (Brush)FindResource(running ? "Green" : row.Starting ? "Amber" : row.Problem is not null ? "Red" : "Muted");
        }
    }
}

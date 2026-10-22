using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using ContextDashboard.Data;

namespace ContextDashboard.Views;

/// <summary>The Model benchmark page. Reads the benchmark when opened and every minute while it stays open.</summary>
public partial class BenchmarkPage : UserControl
{
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMinutes(1) };
    private MainViewModel? _vm;

    public BenchmarkPage()
    {
        InitializeComponent();
        _timer.Tick += async (_, _) =>
        {
            if (_vm is not null && System.Windows.Application.Current.MainWindow?.WindowState != WindowState.Minimized) await _vm.RefreshBenchmarkAsync();
        };
        Loaded += async (_, _) =>
        {
            _vm = (MainViewModel)DataContext;
            _vm.BenchmarkVisible = true;
            _timer.Start();
            await _vm.RefreshBenchmarkAsync();
        };
        Unloaded += (_, _) =>
        {
            _timer.Stop();
            if (_vm is not null) _vm.BenchmarkVisible = false;
        };
    }

    private async void OnRefresh(object sender, RoutedEventArgs e) => await ((MainViewModel)DataContext).RefreshBenchmarkAsync();

    private async void OnMakeChampion(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is not BenchmarkRow row) return;
        var answer = System.Windows.MessageBox.Show(
            $"Make {row.Display} the champion?\n\nIts decisions will decide what is saved from now on. Capture restarts to load it. "
            + $"Nothing is deleted: the current champion keeps scoring in shadow mode, and you can switch back here at any time.\n\n"
            + $"Live AP {row.ApText} on {row.Graded:N0} graded screens.",
            "Make champion", MessageBoxButton.OKCancel, MessageBoxImage.Question);
        if (answer == MessageBoxResult.OK) await ((MainViewModel)DataContext).MakeChampionAsync(row);
    }
}

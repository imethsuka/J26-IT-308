using System.Windows;
using System.Windows.Controls;

namespace ContextDashboard.Views;

/// <summary>Collector/worker status and Start/Stop, shown at the top of every Memory page (it binds to the shared view-model).</summary>
public partial class CaptureBar : UserControl
{
    public CaptureBar() => InitializeComponent();

    private async void OnStart(object sender, RoutedEventArgs e) => await ((MainViewModel)DataContext).StartAsync();
    private async void OnStop(object sender, RoutedEventArgs e) => await ((MainViewModel)DataContext).StopAsync();
}

using System.Windows;
using System.Windows.Controls;

namespace ContextDashboard.Views;

public partial class ModelsPage : UserControl
{
    public ModelsPage() => InitializeComponent();

    private async void OnRunLearning(object sender, RoutedEventArgs e) => await ((MainViewModel)DataContext).RunLearningAsync();
    private async void OnRestart(object sender, RoutedEventArgs e) => await ((MainViewModel)DataContext).RestartCaptureAsync();
}

using System.Windows.Controls;

namespace ContextDashboard.Views;

public partial class OverviewPage : UserControl
{
    public OverviewPage() => InitializeComponent();

    private async void OnRangeChecked(object sender, System.Windows.RoutedEventArgs e)
    {
        if (!IsLoaded || DataContext is not MainViewModel vm) return;
        vm.ActivityDays = int.Parse((string)((RadioButton)sender).Tag);
        await vm.RefreshAsync();
    }
}

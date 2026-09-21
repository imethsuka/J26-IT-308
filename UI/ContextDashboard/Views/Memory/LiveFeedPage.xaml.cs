using System.Windows;
using System.Windows.Controls;

namespace ContextDashboard.Views;

public partial class LiveFeedPage : UserControl
{
    public LiveFeedPage() => InitializeComponent();

    private async void OnFilterChanged(object sender, RoutedEventArgs e)
    {
        if (!IsLoaded) return;
        var vm = (MainViewModel)DataContext;
        vm.FeedFilter = FilterBox.Text;
        vm.FeedDecision = (DecisionBox.SelectedItem as ComboBoxItem)?.Content as string ?? "all";
        await vm.RefreshAsync();
    }
}

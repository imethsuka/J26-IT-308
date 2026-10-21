using System.Windows;
using System.Windows.Controls;
using ContextDashboard.Services;

namespace ContextDashboard.Views;

/// <summary>The screen-text opt-in. Turning it on always shows the consent text first; nothing changes unless the user agrees.</summary>
public partial class ScreenTextPage : UserControl
{
    public ScreenTextPage() => InitializeComponent();

    private MainViewModel Vm => (MainViewModel)DataContext;

    private async void OnScreenTextClicked(object sender, RoutedEventArgs e)
    {
        var wantOn = ScreenTextBox.IsChecked == true;
        if (wantOn)
        {
            var answer = System.Windows.MessageBox.Show(
                Window.GetWindow(this), AppSettings.ConsentText + "\n\nTurn on screen text capture?", "Screen text capture",
                MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.No);
            if (answer != MessageBoxResult.Yes)
            {
                ScreenTextBox.IsChecked = false;
                return;
            }
        }
        await Vm.SetScreenTextAsync(wantOn);
        ScreenTextBox.IsChecked = Vm.ScreenTextActive;
    }

    private async void OnKeepUnredactedClicked(object sender, RoutedEventArgs e)
    {
        var wantOn = KeepUnredactedBox.IsChecked == true;
        if (wantOn)
        {
            var answer = System.Windows.MessageBox.Show(
                Window.GetWindow(this),
                "Without a privacy filter nothing is redacted: passwords you can see, card numbers and private messages on the "
                + "screen would be kept in memory entries. Use this only on your own data, for development.\n\nKeep unredacted text?",
                "Developer option", MessageBoxButton.YesNo, MessageBoxImage.Warning, MessageBoxResult.No);
            if (answer != MessageBoxResult.Yes)
            {
                KeepUnredactedBox.IsChecked = false;
                return;
            }
        }
        await Vm.SetKeepUnredactedAsync(wantOn);
        KeepUnredactedBox.IsChecked = Vm.KeepUnredacted;
    }

    private async void OnDeleteScreenText(object sender, RoutedEventArgs e)
    {
        var answer = System.Windows.MessageBox.Show(
            Window.GetWindow(this), "Delete every captured text and every memory entry? This cannot be undone.", "Delete screen text",
            MessageBoxButton.YesNo, MessageBoxImage.Warning, MessageBoxResult.No);
        if (answer == MessageBoxResult.Yes) await Vm.DeleteScreenTextAsync();
    }
}

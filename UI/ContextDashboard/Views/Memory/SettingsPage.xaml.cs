using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using ContextDashboard.Services;

namespace ContextDashboard.Views;

public partial class SettingsPage : UserControl
{
    public SettingsPage()
    {
        InitializeComponent();
        Loaded += (_, _) =>
        {
            AutostartBox.IsChecked = Autostart.IsEnabled();
            if (((MainViewModel)DataContext).Settings.WorkerExe.Length > 0)
            {
                // Installed app: the worker is a frozen exe with an ONNX encoder, so these repo/torch paths do not apply.
                DevPaths.Visibility = Visibility.Collapsed;
                CheckpointPaths.Visibility = Visibility.Collapsed;
            }
        };
    }

    private MainViewModel Vm => (MainViewModel)DataContext;

    private void OnSaveSettings(object sender, RoutedEventArgs e)
    {
        Vm.Settings.Save();
        Vm.StatusMessage = "Settings saved. Restart the dashboard to read from a different database.";
    }

    private void OnOpenLogs(object sender, RoutedEventArgs e)
    {
        Directory.CreateDirectory(AppSettings.LogDir);
        Process.Start(new ProcessStartInfo("explorer.exe", $"\"{AppSettings.LogDir}\"") { UseShellExecute = false })?.Dispose();
    }

    private void OnAutostartClicked(object sender, RoutedEventArgs e)
    {
        try
        {
            Autostart.Set(AutostartBox.IsChecked == true);
        }
        catch (Exception ex) when (ex is UnauthorizedAccessException or System.Security.SecurityException)
        {
            AutostartBox.IsChecked = Autostart.IsEnabled();
            Vm.StatusMessage = "Could not change the startup setting: " + ex.Message;
        }
    }
}

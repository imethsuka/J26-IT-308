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
            FillModels();
            if (((MainViewModel)DataContext).Settings.WorkerExe.Length > 0)
            {
                // Installed app: the worker is a frozen exe with an ONNX encoder, so these repo/torch paths do not apply.
                DevPaths.Visibility = Visibility.Collapsed;
                CheckpointPaths.Visibility = Visibility.Collapsed;
            }
        };
    }

    private MainViewModel Vm => (MainViewModel)DataContext;

    public sealed record ModelChoice(string Label, string Path);

    private bool _fillingModels;

    /// <summary>"Automatic" plus every model found. The saved choice is selected; a choice whose file is gone shows Automatic.</summary>
    private void FillModels()
    {
        var settings = Vm.Settings;
        var choices = new List<ModelChoice> { new("Automatic: largest model on a dedicated GPU, smallest on the CPU", "") };
        choices.AddRange(settings.AvailableLlmModels().Select(f =>
            new ModelChoice($"{System.IO.Path.GetFileNameWithoutExtension(f)}  ({new FileInfo(f).Length / 1e9:0.0} GB)", f)));
        _fillingModels = true;
        ModelBox.ItemsSource = choices;
        ModelBox.SelectedItem = choices.FirstOrDefault(c => c.Path.Length > 0 && string.Equals(c.Path, settings.LlmModelPath, StringComparison.OrdinalIgnoreCase))
                                ?? choices[0];
        _fillingModels = false;
        ShowModelNote();
    }

    private void OnModelChosen(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (_fillingModels || ModelBox.SelectedItem is not ModelChoice choice) return;
        Vm.Settings.LlmModelPath = choice.Path;
        Vm.Settings.Save();
        ShowModelNote();
    }

    private void ShowModelNote()
    {
        var model = Vm.Settings.ResolveLlmModel();
        ModelNote.Text = model is null
            ? $"No language model found. Put a .gguf chat model in {AppSettings.LlmDir} and summaries start on the next learning run."
            : File.Exists(model)
                ? "Runs on the dedicated GPU when it fits (partly, if needed), else on the CPU. Applies to the next run. Nothing leaves this PC."
                : "The GPU gets the largest model that fits, the CPU the smallest. Applies to the next run. Nothing leaves this PC.";
    }

    private async void OnSaveRetention(object sender, RoutedEventArgs e) => await Vm.ApplyRetentionAsync();

    private async void OnRetentionDefaults(object sender, RoutedEventArgs e) => await Vm.ApplyRetentionAsync(defaults: true);

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

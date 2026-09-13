using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using CAPRLMComparison.Models;
using CAPRLMComparison.Services;
using Microsoft.Win32;

namespace CAPRLMComparison.Views;

public partial class ComparePage : UserControl
{
    private const long MaxImageBytes = 10 * 1024 * 1024;

    private ApiClient _api = null!;
    private string? _imagePath;

    public ComparePage() => InitializeComponent();

    public void Init(ApiClient api) => _api = api;

    private void OnPickImage(object sender, RoutedEventArgs e)
    {
        var dlg = new OpenFileDialog { Filter = "Images|*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.tif;*.tiff|All files|*.*" };
        if (dlg.ShowDialog() != true) return;
        _imagePath = dlg.FileName;
        ImageLabel.Text = $"{Path.GetFileName(_imagePath)} — OCR path (source: ocr). The text box is ignored.";
        ClearImageButton.Visibility = Visibility.Visible;
        SourceBox.SelectedIndex = 1;
        SourceBox.IsEnabled = false;
    }

    private void OnClearImage(object sender, RoutedEventArgs e)
    {
        _imagePath = null;
        ImageLabel.Text = "No screenshot — the text box is used (source: UI Automation).";
        ClearImageButton.Visibility = Visibility.Collapsed;
        SourceBox.IsEnabled = true;
    }

    private async void OnCompare(object sender, RoutedEventArgs e)
    {
        // Drop any previous cards first: a failed run must not leave an old result looking current.
        Cards.ItemsSource = null;
        OcrPanel.Visibility = Visibility.Collapsed;

        if (string.IsNullOrWhiteSpace(AppBox.Text))
        {
            ShowStatus("Enter the app name (e.g. chrome) — the context is part of the input.", isError: true);
            return;
        }
        if (_imagePath is null && string.IsNullOrWhiteSpace(InputText.Text))
        {
            ShowStatus("Enter some text, or choose a screenshot.", isError: true);
            return;
        }

        var source = ((ComboBoxItem)SourceBox.SelectedItem).Content!.ToString()!;
        var context = new ContextDto(AppBox.Text.Trim(), NullIfBlank(DomainBox.Text), NullIfBlank(TitleBox.Text), source);

        CompareButton.IsEnabled = false;
        ShowStatus(_imagePath is null ? "Comparing…" : "Running OCR and comparing… (first run can take a while)", isError: false);
        try
        {
            CompareResponse response;
            if (_imagePath is null)
            {
                response = await _api.CompareAsync(InputText.Text, context);
            }
            else
            {
                if (new FileInfo(_imagePath).Length > MaxImageBytes)
                {
                    ShowStatus("That screenshot is over 10 MB — choose a smaller image.", isError: true);
                    return;
                }
                var bytes = await File.ReadAllBytesAsync(_imagePath);
                response = await _api.CompareImageAsync(bytes, context);
            }

            Cards.ItemsSource = MetricCharts.FilterOrder
                .Where(response.Results.ContainsKey)
                .Select(n => new FilterCard(n, response.Results[n]))
                .ToList();
            if (response.OcrText is not null)
            {
                OcrBox.Text = response.OcrText;
                OcrPanel.Visibility = Visibility.Visible;
            }
            StatusBanner.Visibility = Visibility.Collapsed;
        }
        catch (ServiceUnreachableException ex) { ShowStatus(ex.Message, isError: true); }
        catch (ServiceErrorException ex) { ShowStatus(ex.Message, isError: true); }
        catch (Exception ex) { ShowStatus($"Unexpected error: {ex.Message}", isError: true); }
        finally { CompareButton.IsEnabled = true; }
    }

    private void ShowStatus(string message, bool isError)
    {
        StatusText.Text = message;
        StatusText.Foreground = new SolidColorBrush(isError ? Color.FromRgb(0xB3, 0x26, 0x1E) : Color.FromRgb(0x4B, 0x55, 0x63));
        StatusBanner.Background = new SolidColorBrush(isError ? Color.FromRgb(0xFD, 0xEC, 0xEA) : Color.FromRgb(0xEE, 0xF2, 0xF7));
        StatusBanner.Visibility = Visibility.Visible;
    }

    private static string? NullIfBlank(string s) => string.IsNullOrWhiteSpace(s) ? null : s.Trim();
}

/// <summary>Display shape of one result card.</summary>
public sealed class FilterCard
{
    private static readonly Dictionary<string, string> Titles = new()
    {
        ["caprlm"] = "CAPRLM",
        ["rule_based"] = "Rule-based",
        ["static_masking"] = "Static masking",
        ["anonymization"] = "Anonymization",
        ["pseudonymization"] = "Pseudonymization",
    };

    private readonly FilterResultDto _r;
    private readonly bool _primary;

    public FilterCard(string name, FilterResultDto result)
    {
        _r = result;
        _primary = name == "caprlm";
        Title = Titles.GetValueOrDefault(name, name);
    }

    public string Title { get; }
    public string FilterVersion => _r.FilterVersion;
    public string Text => _r.Blocked ? "(blocked — no text returned)" : _r.Text;
    public string RedactionsText => $"{_r.Redactions} redaction{(_r.Redactions == 1 ? "" : "s")}";
    public string? Error => _r.Error;
    public IEnumerable<string> AuditLines => _r.Audit.Select(a => $"{a.Category} · {a.RiskTier} · {a.Action}");
    public Visibility BlockedVisibility => _r.Blocked ? Visibility.Visible : Visibility.Collapsed;
    public Visibility ErrorVisibility => string.IsNullOrEmpty(_r.Error) ? Visibility.Collapsed : Visibility.Visible;
    public Visibility NoAuditVisibility => _r.Audit.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    public Thickness BorderThickness => new(_primary ? 2 : 1);
    public Brush BorderBrush => new SolidColorBrush(_primary ? Color.FromRgb(0x25, 0x63, 0xEB) : Color.FromRgb(0xE1, 0xE5, 0xEA));
}

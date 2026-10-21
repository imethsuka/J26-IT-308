using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using CAPRLMComparison.Models;
using CAPRLMComparison.Services;
using ScottPlot.WPF;

namespace CAPRLMComparison.Views;

public partial class MetricsPage : UserControl
{
    private ApiClient _api = null!;
    private readonly List<WpfPlot> _plots = [];

    public MetricsPage() => InitializeComponent();

    public bool HasResults { get; private set; }

    public void Init(ApiClient api) => _api = api;

    private async void OnRefresh(object sender, RoutedEventArgs e) => await LoadAsync();

    public async Task LoadAsync()
    {
        HasResults = false;
        DatasetBanner.Visibility = Visibility.Collapsed;
        ChartGrid.Visibility = Visibility.Collapsed;
        NotesPanel.Visibility = Visibility.Collapsed;
        ShowStatus("Loading evaluation results…", isError: false, canRetry: false);
        try
        {
            var results = await _api.GetEvalResultsAsync();
            Render(results);
            StatusBanner.Visibility = Visibility.Collapsed;
            HasResults = true;
        }
        catch (ServiceUnreachableException ex) { ShowStatus(ex.Message, isError: true, canRetry: true); }
        catch (ServiceErrorException ex) { ShowStatus(ex.Message, isError: true, canRetry: true); }
        catch (Exception ex) { ShowStatus($"Could not read the evaluation results: {ex.Message}", isError: true, canRetry: true); }
    }

    private void Render(EvalResults r)
    {
        bool placeholder = r.Dataset.Contains("placeholder", StringComparison.OrdinalIgnoreCase);
        bool unaudited = r.Dataset.Contains("unaudited", StringComparison.OrdinalIgnoreCase);
        bool warn = placeholder || unaudited;

        DatasetTitle.Text = $"Dataset: {r.Dataset}";
        DatasetWarning.Text = unaudited
            ? "UNAUDITED DATA — small, LLM-generated, labels not checked by a person. Indicative only; not reportable " +
              "until a sample has been human-audited."
            : placeholder
            ? "PLACEHOLDER DATA — the labels come from a hand-written table, so these numbers only show that the " +
              "pipeline runs end to end. They are not research results and must not be reported."
            : "Labelled evaluation set.";
        DatasetMeta.Text = DescribeRun(r);

        var (bg, fg) = warn
            ? (Color.FromRgb(0xFD, 0xEC, 0xEA), Color.FromRgb(0xB3, 0x26, 0x1E))
            : (Color.FromRgb(0xEA, 0xF4, 0xEC), Color.FromRgb(0x1E, 0x6B, 0x35));
        DatasetBanner.Background = new SolidColorBrush(bg);
        DatasetBanner.BorderBrush = new SolidColorBrush(fg);
        DatasetBanner.BorderThickness = new Thickness(warn ? 2 : 1);
        DatasetTitle.Foreground = DatasetWarning.Foreground = new SolidColorBrush(fg);
        DatasetMeta.Foreground = new SolidColorBrush(Color.FromRgb(0x4B, 0x55, 0x63));
        DatasetBanner.Visibility = Visibility.Visible;

        // Charts are rebuilt on every load so a refresh can never show a stale mixture.
        ChartGrid.Children.Clear();
        _plots.Clear();
        foreach (var spec in MetricCharts.Specs)
        {
            var host = new Border
            {
                Background = Brushes.White,
                BorderBrush = new SolidColorBrush(Color.FromRgb(0xE1, 0xE5, 0xEA)),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Margin = new Thickness(0, 0, 8, 8),
                Padding = new Thickness(6),
                Height = 280,
            };
            var plot = new WpfPlot();
            MetricCharts.Build(plot.Plot, spec, r, warn);
            plot.Refresh();
            host.Child = plot;
            ChartGrid.Children.Add(host);
            _plots.Add(plot);
        }
        ChartGrid.Visibility = Visibility.Visible;

        NotesList.ItemsSource = r.Notes ?? [];
        NotesPanel.Visibility = r.Notes is { Count: > 0 } ? Visibility.Visible : Visibility.Collapsed;
    }

    private static string DescribeRun(EvalResults r)
    {
        var parts = new List<string>();
        if (r.Model is not null)
        {
            var sel = r.Model.Selection is { } s
                ? $" (chosen by CV macro-F1: {string.Join(", ", s.Scores.Select(kv => $"{kv.Key} {kv.Value:0.000}"))})"
                : "";
            parts.Add($"CAPRLM model: {r.Model.Version}{sel}");
        }
        if (r.Detection?.NerModel is { } ner) parts.Add($"NER: {ner}");
        if (r.SimilarityMethod is { } sim) parts.Add($"Similarity: {sim} (lexical proxy)");
        if (r.DatasetDetail is { } d)
            parts.Add($"{d.NTrain} train / {d.NTest} test scenarios, {d.NTestSensitiveSpans} sensitive test spans");
        if (r.GeneratedAt is { } at) parts.Add($"Generated {at}");
        return string.Join("  ·  ", parts);
    }

    private void ShowStatus(string message, bool isError, bool canRetry)
    {
        StatusText.Text = message;
        StatusText.Foreground = new SolidColorBrush(isError ? Color.FromRgb(0xB3, 0x26, 0x1E) : Color.FromRgb(0x4B, 0x55, 0x63));
        StatusBanner.Background = new SolidColorBrush(isError ? Color.FromRgb(0xFD, 0xEC, 0xEA) : Color.FromRgb(0xEE, 0xF2, 0xF7));
        RetryButton.Visibility = canRetry ? Visibility.Visible : Visibility.Collapsed;
        StatusBanner.Visibility = Visibility.Visible;
    }
}

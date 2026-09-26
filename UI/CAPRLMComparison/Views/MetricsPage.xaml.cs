using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using CAPRLMComparison.Models;
using CAPRLMComparison.Services;
using ScottPlot.WPF;

namespace CAPRLMComparison.Views;

public partial class MetricsPage : UserControl
{
    private ApiClient _api = null!;

    public MetricsPage() => InitializeComponent();

    public bool HasResults { get; private set; }

    public void Init(ApiClient api) => _api = api;

    private async void OnRefresh(object sender, RoutedEventArgs e) => await LoadAsync();

    public async Task LoadAsync()
    {
        HasResults = false;
        LiveNote.Visibility = Visibility.Collapsed;
        ExplainPanel.Visibility = Visibility.Collapsed;
        ShowStatus("Reading the model\u2026", isError: false, canRetry: false);
        try
        {
            await LoadExplainAsync();
            StatusBanner.Visibility = Visibility.Collapsed;
            HasResults = true;
        }
        catch (ServiceUnreachableException ex) { ShowStatus(ex.Message, isError: true, canRetry: true); }
        catch (ServiceErrorException ex) { ShowStatus(ex.Message, isError: true, canRetry: true); }
        catch (Exception ex) { ShowStatus($"Could not read the model: {ex.Message}", isError: true, canRetry: true); }
    }

    // ---- how the model works: weights, the trade-off, per-category results -------------------------------------------------

    private async Task LoadExplainAsync()
    {
        ExplainNote.Visibility = Visibility.Collapsed;
        var info = await _api.GetModelInfoAsync();          // an error here is the service's: LoadAsync shows it
        ShowEffects(info);
        ExplainPanel.Visibility = Visibility.Visible;
        try
        {
            var curve = await _api.GetModelCurveAsync();
            ShowCurve(curve);
        }
        catch (ServiceErrorException ex)
        {
            ExplainNote.Text = "The trade-off and per-category views need the labelled test scenarios: " + ex.Message;
            ExplainNote.Visibility = Visibility.Visible;
        }
        LiveNote.Visibility = Visibility.Visible;
    }

    private void ShowEffects(ModelInfoDto info)
    {
        EffectsGrid.Children.Clear();
        if (!info.Explainable)
        {
            EffectsIntro.Text = $"{info.Version} is not a linear model, so its reasons cannot be read from weights.";
            return;
        }
        EffectsIntro.Text = $"{info.Version}, trained on {info.TrainedOn ?? "unknown data"}, reads {info.NFeatures:N0} features. For each risk level these are what push " +
                            "the model toward it most (weights learned in training; a longer bar is a stronger push). They describe this model on this " +
                            "synthetic data, not a rule about privacy.";
        var max = info.Effects.Values.SelectMany(v => v).Select(f => f.Weight).DefaultIfEmpty(1).Max();
        foreach (var tier in new[] { "low", "medium", "high" }.Where(info.Effects.ContainsKey))
        {
            var column = new StackPanel { Margin = new Thickness(0, 0, 14, 0) };
            column.Children.Add(new TextBlock { Text = tier.ToUpperInvariant(), FontFamily = new FontFamily("Consolas"), FontSize = 11, Foreground = Muted, Margin = new Thickness(0, 0, 0, 6) });
            var rows = info.Effects[tier].Take(6).Select(f => new Bar(f.Label, f.Weight.ToString("+0.0;-0.0"), f.Weight / max)).ToList();
            var list = new ItemsControl { ItemsSource = rows, ItemTemplate = BarTemplate() };
            column.Children.Add(list);
            EffectsGrid.Children.Add(column);
        }
    }

    private void ShowCurve(CurveDto curve)
    {
        CurveIntro.Text = $"For each risk threshold: of the {curve.NSpans} spans the detectors found in the test scenarios ({curve.NSensitive} labelled sensitive), " +
                          "how many sensitive ones are caught (recall) and how many protected ones really were sensitive (precision). Lower threshold = protects more.";
        ForwardWheel(CurvePlot);
        var plot = CurvePlot.Plot;
        plot.Clear();
        var tau = curve.Points.Select(p => p.Tau).ToArray();
        var precision = plot.Add.Scatter(tau, curve.Points.Select(p => p.Precision).ToArray());
        precision.Color = ScottPlot.Color.FromHex("#7C6BB5");
        precision.LegendText = "precision";
        var recall = plot.Add.Scatter(tau, curve.Points.Select(p => p.Recall).ToArray());
        recall.Color = ScottPlot.Color.FromHex("#3F7D5E");
        recall.LegendText = "recall";
        foreach (var (name, value) in curve.Presets.OrderBy(kv => kv.Value))
        {
            var line = plot.Add.VerticalLine(value);
            line.Color = ScottPlot.Color.FromHex(name switch { "strict" => "#B85C5C", "balanced" => "#C48A3A", _ => "#3F6F8F" });
            line.LinePattern = ScottPlot.LinePattern.Dashed;
            // named in the legend, not on the line: Strict and Balanced sit almost on top of each other and clip at the top edge
            line.LegendText = $"{char.ToUpperInvariant(name[0]) + name[1..]} (threshold {value:0.00})";
        }
        plot.Axes.SetLimits(0, 1, 0.5, 1.05);
        plot.XLabel("risk threshold (a span is protected at or above it)");
        plot.ShowLegend(ScottPlot.Alignment.LowerLeft);
        CurvePlot.Refresh();

        PresetLines.ItemsSource = new[] { "relaxed", "balanced", "strict" }.Where(curve.Presets.ContainsKey).Select(name =>
        {
            var p = curve.Points.OrderBy(x => Math.Abs(x.Tau - curve.Presets[name])).First();
            return $"{char.ToUpperInvariant(name[0]) + name[1..]} (threshold {p.Tau:0.00}): catches {p.Recall:P0} of sensitive spans; " +
                   $"{p.Precision:P0} of what it protects is sensitive.";
        }).ToList();

        CategoryIntro.Text = $"At the Balanced level, per kind of entity, over the same test scenarios. Counts are small: read the number beside each bar.";
        CategoryList.ItemsSource = curve.Categories.Select(c => new Bar(c.Category.Replace('_', ' ').ToLowerInvariant(),
            $"{c.Recall:P0}  n={c.Sensitive}", c.Recall)).ToList();
    }

    /// <summary>
    /// A chart swallows the mouse wheel (it would zoom), so the page stops scrolling whenever the pointer is over one. Zoom is turned
    /// off and the wheel is handed to the chart's parent, which bubbles it up to the page's scroll viewer.
    /// </summary>
    private static void ForwardWheel(WpfPlot plot)
    {
        plot.UserInputProcessor.Disable();
        plot.PreviewMouseWheel += (sender, e) =>
        {
            if (sender is not FrameworkElement { Parent: UIElement parent }) return;
            e.Handled = true;
            parent.RaiseEvent(new MouseWheelEventArgs(e.MouseDevice, e.Timestamp, e.Delta)
            {
                RoutedEvent = UIElement.MouseWheelEvent,
                Source = sender,
            });
        };
    }

    private static readonly Brush Muted = new SolidColorBrush(Color.FromRgb(0x7A, 0x7F, 0x78));

    private sealed record Bar(string Label, string ValueText, double Share);

    private static DataTemplate BarTemplate()
    {
        const string xaml = """
            <DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">
              <StackPanel Margin="0,0,0,8">
                <DockPanel>
                  <TextBlock DockPanel.Dock="Right" Text="{Binding ValueText}" FontFamily="Consolas" FontSize="10" Foreground="#7A7F78" Margin="8,0,0,0" />
                  <TextBlock Text="{Binding Label}" FontSize="12" TextWrapping="Wrap" />
                </DockPanel>
                <ProgressBar Maximum="1" Value="{Binding Share, Mode=OneWay}" Height="5" Margin="0,3,0,0" Foreground="#3F7D5E" Background="#E8E5DB" BorderThickness="0" />
              </StackPanel>
            </DataTemplate>
            """;
        return (DataTemplate)System.Windows.Markup.XamlReader.Parse(xaml);
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

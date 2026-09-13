using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using CAPRLMComparison.Models;
using CAPRLMComparison.Services;

namespace CAPRLMComparison.Views;

public partial class PolicyPage : UserControl
{
    private ApiClient _api = null!;
    private PolicyDto? _policy;
    private bool _busy;

    public PolicyPage() => InitializeComponent();

    public void Init(ApiClient api) => _api = api;

    private async void OnRefresh(object sender, RoutedEventArgs e) => await LoadAsync();

    /// <summary>Reloads policy and stats. Called every time the page is opened, so it is never stale.</summary>
    public async Task LoadAsync()
    {
        try
        {
            var policy = await _api.GetPolicyAsync();
            var stats = await _api.GetStatsAsync();
            ShowPolicy(policy);
            ShowStats(stats);
            StatusBanner.Visibility = Visibility.Collapsed;
        }
        catch (ServiceUnreachableException ex) { ShowStatus(ex.Message, isError: true); }
        catch (ServiceErrorException ex) { ShowStatus(ex.Message, isError: true); }
        catch (Exception ex) { ShowStatus($"Could not load the policy: {ex.Message}", isError: true); }
    }

    // ---- edits ----------------------------------------------------------------------------------

    private async void OnActionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_busy || _policy is null || sender is not ComboBox box || box.SelectedValue is not string action) return;
        var id = (string)box.Tag;
        if (_policy.Groups.First(g => g.Id == id).Action == action) return;   // initial binding, not a user edit
        await UpdateAsync(new { groups = new Dictionary<string, object> { [id] = new { action } } });
    }

    private async void OnEnabledClicked(object sender, RoutedEventArgs e)
    {
        if (_busy || _policy is null) return;
        var toggle = (ToggleButton)sender;
        await UpdateAsync(new
        {
            groups = new Dictionary<string, object> { [(string)toggle.Tag] = new { enabled = toggle.IsChecked == true } },
        });
    }

    private async void OnMaskSendClicked(object sender, RoutedEventArgs e)
    {
        if (_busy || _policy is null) return;
        await UpdateAsync(new { llm = new { mask_before_send = ((ToggleButton)sender).IsChecked == true } });
    }

    private async void OnLlmFieldCommitted(object sender, RoutedEventArgs e)
    {
        if (_busy || _policy is null) return;
        var provider = ProviderBox.Text.Trim();
        var model = ModelBox.Text.Trim();
        if (provider == _policy.Llm.Provider && model == _policy.Llm.Model) return;
        await UpdateAsync(new { llm = new { provider, model } });
    }

    private async Task UpdateAsync(object update)
    {
        _busy = true;
        try
        {
            ShowPolicy(await _api.UpdatePolicyAsync(update));
            StatusBanner.Visibility = Visibility.Collapsed;
        }
        catch (ServiceUnreachableException ex) { ShowStatus(ex.Message, isError: true); }
        catch (ServiceErrorException ex) { ShowStatus(ex.Message, isError: true); }
        finally { _busy = false; }
        // The edit may have failed: reload so the controls show what the service actually holds.
        if (StatusBanner.Visibility == Visibility.Visible) await LoadAsync();
    }

    // ---- render ----------------------------------------------------------------------------------------

    private void ShowPolicy(PolicyDto p)
    {
        _busy = true;           // setting controls from a result must not read as user edits
        try
        {
            _policy = p;
            GroupList.ItemsSource = p.Groups.Select(g => new GroupRow(g)).ToList();
            EnforcedChip.Text = $"{p.Groups.Count(g => g.Enabled)} enforced";
            MaskSendToggle.IsChecked = p.Llm.MaskBeforeSend;
            ProviderBox.Text = p.Llm.Provider;
            ModelBox.Text = p.Llm.Model;
        }
        finally { _busy = false; }
    }

    private void ShowStats(StatsDto s)
    {
        FlaggedStat.Text = s.Flagged7d.ToString("N0");
        RedactedStat.Text = s.Redacted7d.ToString("N0");
        RulesStat.Text = s.LearnedRules.ToString();
        RulesOf.Text = $"/ {s.LearnableCategories}";
        if (s.UtilityKept is { } u)
        {
            UtilityStat.Text = Math.Round(u * 100).ToString("0");
            UtilityUnit.Text = "%";
        }
        else
        {
            UtilityStat.Text = "—";
            UtilityUnit.Text = "";
        }

        BuildChart(s.Weekly);
        ChartNote.Text = s.Reviews7d == 0
            ? "No activity yet. Each capture you analyze on Privacy review is counted here (counts only, never content)."
            : $"{s.Reviews7d} capture{(s.Reviews7d == 1 ? "" : "s")} reviewed in the last 7 days. " +
              "Utility kept = share of the text's characters left readable after masking, averaged over those captures.";
    }

    private void BuildChart(List<DayStat> days)
    {
        var plot = WeekPlot.Plot;
        plot.Clear();
        // Flagged behind, redacted on top, at the same positions: the gap is what was flagged but left visible.
        plot.Add.Bars(days.Select((d, i) => new ScottPlot.Bar
            { Position = i, Value = d.Flagged, Size = 0.55, FillColor = ScottPlot.Color.FromHex("#D9D5C7") }).ToArray());
        plot.Add.Bars(days.Select((d, i) => new ScottPlot.Bar
            { Position = i, Value = d.Redacted, Size = 0.55, FillColor = ScottPlot.Color.FromHex("#3F7D5E") }).ToArray());

        plot.Axes.Bottom.TickGenerator = new ScottPlot.TickGenerators.NumericManual(
            days.Select((d, i) => new ScottPlot.Tick(i, d.Weekday)).ToArray());
        var top = Math.Max(4, days.Max(d => Math.Max(d.Flagged, d.Redacted)) * 1.2);
        plot.Axes.SetLimitsY(0, top);
        plot.Axes.SetLimitsX(-0.6, days.Count - 0.4);

        plot.FigureBackground.Color = ScottPlot.Color.FromHex("#FCFBF8");
        plot.DataBackground.Color = ScottPlot.Color.FromHex("#FCFBF8");
        plot.Grid.MajorLineColor = ScottPlot.Color.FromHex("#E6E2D8");
        plot.Axes.Bottom.TickLabelStyle.ForeColor = ScottPlot.Color.FromHex("#7A7F78");
        plot.Axes.Left.TickLabelStyle.ForeColor = ScottPlot.Color.FromHex("#7A7F78");
        WeekPlot.Refresh();
    }

    private void ShowStatus(string message, bool isError)
    {
        StatusText.Text = message;
        StatusText.Foreground = (Brush)FindResource(isError ? "Red" : "Muted");
        StatusBanner.Background = (Brush)FindResource(isError ? "RedSoft" : "InsetBg");
        StatusBanner.Visibility = Visibility.Visible;
    }
}

public sealed class GroupRow(GroupPolicyDto g)
{
    public string Id => g.Id;
    public string Label => g.Label;
    public string Action => g.Action;
    public bool Enabled => g.Enabled;
    public string Categories => string.Join(" · ", g.Categories.Select(c => c.Replace('_', ' ').ToLowerInvariant()));
}

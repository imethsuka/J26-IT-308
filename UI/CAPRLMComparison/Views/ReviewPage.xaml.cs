using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Documents;
using System.Windows.Media;
using CAPRLMComparison.Models;
using CAPRLMComparison.Services;

namespace CAPRLMComparison.Views;

public partial class ReviewPage : UserControl
{
    private const string SampleText =
        "From: HR Office <hr@example.com>\nTo: chathura.perera@example.com\nDate: September 3, 2026\n\n" +
        "Hi Chathura,\n\nFollowing the DSIS Research Group review, your revised monthly salary is " +
        "Rs. 185,000 effective next cycle. Your employee record NIC 199512345678 has been updated " +
        "accordingly.\n\nRegards,\nHR";

    private ApiClient _api = null!;
    private readonly Dictionary<string, string> _overrides = new();
    private ReviewResult? _last;
    private string? _level;          // null until the first result tells us the saved level
    private bool _syncing;           // true while we set controls from a result (suppresses their events)
    private bool _busy;

    public ReviewPage()
    {
        InitializeComponent();
        InputText.Text = SampleText;
    }

    public void Init(ApiClient api) => _api = api;

    // ---- actions ----------------------------------------------------------------------------

    private async void OnAnalyze(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(AppBox.Text))
        {
            ShowStatus("Enter the app name (e.g. outlook) — the context is part of the input.", isError: true);
            return;
        }
        if (string.IsNullOrWhiteSpace(InputText.Text))
        {
            ShowStatus("Enter some text to review.", isError: true);
            return;
        }
        _overrides.Clear();           // a new capture: entity ids belong to the old text
        await RunAsync(record: true, status: "Analyzing…");
    }

    private async void OnLevelChecked(object sender, RoutedEventArgs e)
    {
        if (_syncing || _last is null) return;
        var level = (string)((RadioButton)sender).Tag;
        if (level == _level) return;
        _level = level;
        try { await _api.UpdatePolicyAsync(new { level }); }   // the level is a saved default, shared with Policy & risk
        catch (Exception ex) { ShowStatus(ex.Message, isError: true); return; }
        await RunAsync(record: false);
    }

    private async void OnEntityToggled(object sender, RoutedEventArgs e)
    {
        if (_busy || _last is null) return;
        var toggle = (ToggleButton)sender;
        var id = (string)toggle.Tag;
        var entity = _last.Entities.First(x => x.Id == id);
        var decision = toggle.IsChecked == true ? "masked" : "visible";
        _overrides[id] = decision;
        try { await _api.AddCorrectionAsync(entity.Category, decision); }
        catch (Exception ex) { ShowStatus(ex.Message, isError: true); return; }
        await RunAsync(record: false);
    }

    private async void OnMaskBeforeSend(object sender, RoutedEventArgs e)
    {
        if (_syncing || _last is null) return;
        var on = ((ToggleButton)sender).IsChecked == true;
        try { await _api.UpdatePolicyAsync(new { llm = new { mask_before_send = on } }); }
        catch (Exception ex) { ShowStatus(ex.Message, isError: true); return; }
        await RunAsync(record: false);
    }

    private async void OnResetProfile(object sender, RoutedEventArgs e)
    {
        try { ShowProfile(await _api.ResetProfileAsync()); }
        catch (Exception ex) { ShowStatus(ex.Message, isError: true); return; }
        if (_last is not null) await RunAsync(record: false);
    }

    // ---- request / render -------------------------------------------------------------------------

    private async Task RunAsync(bool record, string? status = null)
    {
        if (_busy) return;
        _busy = true;
        AnalyzeButton.IsEnabled = false;
        if (status is not null) ShowStatus(status, isError: false);
        try
        {
            var ctx = new ContextDto(AppBox.Text.Trim(), NullIfBlank(DomainBox.Text), NullIfBlank(TitleBox.Text), "uia");
            var result = await _api.ReviewAsync(InputText.Text, ctx, _level, _overrides, record);
            var profile = await _api.GetProfileAsync();
            Render(result);
            ShowProfile(profile);
            StatusBanner.Visibility = Visibility.Collapsed;
        }
        catch (ServiceUnreachableException ex) { Fail(ex.Message); }
        catch (ServiceErrorException ex) { Fail(ex.Message); }
        catch (Exception ex) { Fail($"Unexpected error: {ex.Message}"); }
        finally { _busy = false; AnalyzeButton.IsEnabled = true; }
    }

    /// <summary>A failed run clears the old result: it must not stay on screen looking current.</summary>
    private void Fail(string message)
    {
        _last = null;
        ResultCard.Visibility = OutboundCard.Visibility = Visibility.Collapsed;
        EntityList.ItemsSource = null;
        NoEntities.Visibility = Visibility.Visible;
        ShowStatus(message, isError: true);
    }

    private void Render(ReviewResult r)
    {
        _last = r;
        _level = r.Level;
        _syncing = true;
        try
        {
            LevelRelaxed.IsChecked = r.Level == "relaxed";
            LevelBalanced.IsChecked = r.Level == "balanced";
            LevelStrict.IsChecked = r.Level == "strict";
            MaskBeforeSend.IsChecked = r.Outbound.MaskBeforeSend;
        }
        finally { _syncing = false; }

        TauChip.Text = $"τ {r.Tau:0.00}";
        MaskedChip.Text = $"{r.Masked}/{r.Total} masked";

        ResultCard.Visibility = OutboundCard.Visibility = Visibility.Visible;
        ResultSubtitle.Text = $"Filtered before persistent storage · {r.FilterVersion}";
        ResidualText.Text = r.ResidualRisk.ToString();
        ResidualText.Foreground = ResidualBar.Foreground = RiskBrush(r.ResidualRisk / 100.0);
        ResidualBar.Value = r.ResidualRisk;
        FillSegments(RedactedBlock, r.Segments);

        var o = r.Outbound;
        OutboundSubtitle.Text = o.Connected
            ? $"{o.Provider}{(string.IsNullOrWhiteSpace(o.Model) ? "" : " · " + o.Model)}"
            : "No provider connected — set one in Policy & risk";
        OutboundBlock.Text = o.Text;
        if (o.MaskBeforeSend)
        {
            OutboundBannerText.Text = $"🔒  {o.Masked} of {o.Total} entities masked before leaving this device";
            OutboundBannerText.Foreground = (Brush)FindResource("Green");
            OutboundBanner.Background = (Brush)FindResource("GreenSoft");
        }
        else
        {
            OutboundBannerText.Text = "Masking is off — this capture would leave the device exactly as captured";
            OutboundBannerText.Foreground = (Brush)FindResource("Red");
            OutboundBanner.Background = (Brush)FindResource("RedSoft");
        }

        EntityList.ItemsSource = r.Entities.Select(e => new EntityRow(e)).ToList();
        NoEntities.Text = "Nothing sensitive was detected in this capture.";
        NoEntities.Visibility = r.Entities.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private void FillSegments(TextBlock block, IEnumerable<SegmentDto> segments)
    {
        block.Inlines.Clear();
        foreach (var s in segments)
        {
            switch (s.Kind)
            {
                case "token":
                    block.Inlines.Add(new Run(s.Text)
                    {
                        Background = (Brush)FindResource("PurpleSoft"), Foreground = (Brush)FindResource("Purple"),
                    });
                    break;
                case "mask":
                    block.Inlines.Add(new Run(s.Text)
                    {
                        Background = (Brush)FindResource("Track"), Foreground = (Brush)FindResource("Muted"),
                    });
                    break;
                case "dropped":
                    block.Inlines.Add(new Run("⟨removed⟩")
                    {
                        Background = (Brush)FindResource("RedSoft"), Foreground = (Brush)FindResource("Red"),
                        FontStyle = FontStyles.Italic,
                    });
                    break;
                default:
                    block.Inlines.Add(new Run(s.Text));
                    break;
            }
        }
    }

    private void ShowProfile(ProfileDto p)
    {
        LearnedChip.Text = $"{p.Rules.Count} learned";
        if (p.Rules.Count == 0)
        {
            ProfileText.Text = p.Corrections == 0
                ? "No corrections yet — the defaults reflect the general policy. Adjust an entity and CAPRLM adapts to you."
                : $"{p.Corrections} correction{(p.Corrections == 1 ? "" : "s")} so far. A rule is learned once one choice " +
                  $"leads the other by {p.EvidenceNeeded} for a category.";
        }
        else
        {
            ProfileText.Text = string.Join("\n", p.Rules.Select(r =>
                $"• {r.Category.Replace('_', ' ')} → always {(r.Decision == "masked" ? "masked" : "visible")} " +
                $"({r.Evidence} corrections)"));
        }
        ResetProfileButton.Visibility = p.Corrections > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private void ShowStatus(string message, bool isError)
    {
        StatusText.Text = message;
        StatusText.Foreground = (Brush)FindResource(isError ? "Red" : "Muted");
        StatusBanner.Background = (Brush)FindResource(isError ? "RedSoft" : "InsetBg");
        StatusBanner.Visibility = Visibility.Visible;
    }

    internal static Brush RiskBrush(double risk) => new SolidColorBrush(
        risk >= 0.6 ? Color.FromRgb(0xB8, 0x5C, 0x5C) : risk >= 0.3 ? Color.FromRgb(0xC4, 0x8A, 0x3A) : Color.FromRgb(0x3F, 0x7D, 0x5E));

    private static string? NullIfBlank(string s) => string.IsNullOrWhiteSpace(s) ? null : s.Trim();
}

/// <summary>Display shape of one detected entity.</summary>
public sealed class EntityRow(EntityDto e)
{
    public string Id => e.Id;
    public string Category => e.Category.Replace('_', ' ');
    public string RiskText => $"risk {Math.Round(e.Risk * 100)}";
    public Brush RiskBrush => ReviewPage.RiskBrush(e.Risk);
    public bool IsMasked => e.Masked;
    public string StateText => e.Masked ? "masked" : "visible";
    public string Reason => e.Reason;

    /// <summary>The model's own reasons, strongest first: "+1.9 Where it appears: the site looks like an email site".</summary>
    public string WhyText => e.Why is { Count: > 0 } why
        ? "Why the model scores it this way:\n" + string.Join("\n", why.Select(w =>
            $"{(w.Effect >= 0 ? "+" : "\u2212")}{Math.Abs(w.Effect):0.0}  {w.Family}: {w.Label}"))
        : "";
    public bool HasWhy => WhyText.Length > 0;

    /// <summary>Masked entities show their token only; the value is shown only for entities left visible.</summary>
    public string Display => e.Masked
        ? (e.Action == "drop" ? "⟨removed⟩" : e.Token ?? "")
        : e.Value ?? "";

    public Brush DisplayBrush => new SolidColorBrush(e.Masked ? Color.FromRgb(0x7C, 0x6B, 0xB5) : Color.FromRgb(0x1F, 0x24, 0x21));
}

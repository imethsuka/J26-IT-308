using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using ContextDashboard.Data;
using Brush = System.Windows.Media.Brush;
using Color = System.Windows.Media.Color;

namespace ContextDashboard.Views;

/// <summary>
/// The Importance Engine page. Every number comes from <see cref="EngineStats"/>, i.e. from stored records.
/// What the worker does not persist (a per-record importance score, a learned threshold) is not drawn.
/// </summary>
public partial class EnginePage : UserControl
{
    // Same hues as the privacy pages' palette, one per signal.
    private static readonly Brush[] SignalBrushes =
        [Solid(0x3F, 0x7D, 0x5E), Solid(0x3F, 0x6F, 0x8F), Solid(0x7C, 0x6B, 0xB5), Solid(0xC4, 0x8A, 0x3A), Solid(0xB8, 0x5C, 0x5C)];

    private MainViewModel? _vm;

    public EnginePage()
    {
        InitializeComponent();
        Loaded += async (_, _) =>
        {
            _vm = (MainViewModel)DataContext;
            _vm.PropertyChanged += OnVmChanged;
            _vm.EngineVisible = true;
            Render(_vm.Engine);
            await _vm.RefreshEngineAsync();
        };
        Unloaded += (_, _) =>
        {
            if (_vm is null) return;
            _vm.PropertyChanged -= OnVmChanged;
            _vm.EngineVisible = false;
        };
    }

    private void OnVmChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(MainViewModel.Engine) && _vm is not null) Render(_vm.Engine);
    }

    private void Render(EngineStats s)
    {
        EmptyNote.Visibility = s.Records == 0 ? Visibility.Visible : Visibility.Collapsed;
        EmptyNote.Text = "No records yet. Start capture and the page fills in as the worker stores contexts.";

        PendingValue.Text = s.Records == 0 ? "–" : s.Pending.ToString("N0");
        if (s.CommitRateRecent is { } rate)
        {
            CommitValue.Text = Math.Round(rate * 100).ToString("0");
            CommitUnit.Text = "%";
        }
        else
        {
            CommitValue.Text = "–";
            CommitUnit.Text = "";
        }
        if (s.Shift is { } shift)
        {
            var points = shift * 100;
            ShiftValue.Text = (points >= 0 ? "+" : "") + points.ToString("0.0");
            ShiftUnit.Text = "pts vs prior 7 d";
        }
        else
        {
            ShiftValue.Text = "–";
            ShiftUnit.Text = "needs a 24 h and a prior 7 d window";
        }

        DwellSub.Text = s.SampleSize == 0
            ? "No records in the last 7 days."
            : $"{s.SampleSize:N0} records from the last 7 days · green = saved to memory, grey = discarded";
        Dwell.Buckets = s.Dwell;

        SignalList.ItemsSource = s.Signals.Select((x, i) => new SignalRow(x, SignalBrushes[i % SignalBrushes.Length])).ToList();

        var labelled = s.Records - s.Pending;
        LabelledText.Text = s.Records == 0
            ? "–"
            : $"{labelled:N0} of {s.Records:N0} records ({(double)labelled / s.Records:P0}). Labels arrive after the learning window.";

        PendingChip.Text = $"{s.Pending:N0} waiting";
        NoPending.Visibility = s.PendingNewest.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        PendingList.ItemsSource = s.PendingNewest.Select(p => new PendingView(p)).ToList();
    }

    private static Brush Solid(byte r, byte g, byte b) => new SolidColorBrush(Color.FromRgb(r, g, b));

    public sealed class SignalRow(SignalStat s, Brush brush)
    {
        public string Name => s.Name;
        public double Value => Math.Clamp(s.Value, 0, 1);
        public string ValueText => s.Value.ToString("0.00");
        public string Detail => s.Detail;
        public Brush Brush => brush;
    }

    public sealed class PendingView(PendingRow p)
    {
        public string Headline => string.IsNullOrWhiteSpace(p.Title) ? p.App : $"{p.App} — {p.Title}";
        public string Detail => $"{p.Revisits} revisit{(p.Revisits == 1 ? "" : "s")} · dwell {p.DwellSeconds:0} s · {MainViewModel.Ago(DateTimeOffset.Now - p.Time)}";
        public string Decision => p.Decision == "save" ? "saved" : "discarded";
        public Brush ChipBg => p.Decision == "save" ? Solid(0xE1, 0xEF, 0xE7) : Solid(0xF1, 0xEF, 0xE8);
        public Brush ChipFg => p.Decision == "save" ? Solid(0x3F, 0x7D, 0x5E) : Solid(0x7A, 0x7F, 0x78);
    }
}

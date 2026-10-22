using System.Globalization;
using System.Windows;
using System.Windows.Media;
using ContextDashboard.Data;
using Brush = System.Windows.Media.Brush;
using Brushes = System.Windows.Media.Brushes;
using Color = System.Windows.Media.Color;
using Pen = System.Windows.Media.Pen;
using Point = System.Windows.Point;

namespace ContextDashboard.Views;

/// <summary>
/// One line per model over days, values 0 to 1 shown as percent or as a plain number, with a legend on top. Drawn
/// directly like <see cref="BarChart"/>. A series with a single day is drawn as a dot.
/// </summary>
public sealed class LineChart : FrameworkElement
{
    public static readonly DependencyProperty SeriesProperty = DependencyProperty.Register(
        nameof(Series), typeof(IReadOnlyList<BenchmarkSeries>), typeof(LineChart),
        new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender));

    public static readonly DependencyProperty PercentProperty = DependencyProperty.Register(
        nameof(Percent), typeof(bool), typeof(LineChart),
        new FrameworkPropertyMetadata(true, FrameworkPropertyMetadataOptions.AffectsRender));

    // The privacy pages' palette first, then a few more distinct hues: one per model family.
    public static readonly Color[] Palette =
    [
        Color.FromRgb(0x3F, 0x7D, 0x5E), Color.FromRgb(0x3F, 0x6F, 0x8F), Color.FromRgb(0x7C, 0x6B, 0xB5), Color.FromRgb(0xC4, 0x8A, 0x3A),
        Color.FromRgb(0xB8, 0x5C, 0x5C), Color.FromRgb(0x4A, 0x9C, 0xA8), Color.FromRgb(0x8C, 0x8C, 0x3A), Color.FromRgb(0x99, 0x99, 0x99),
    ];

    private static readonly Brush TextBrush = Freeze(new SolidColorBrush(Color.FromRgb(0x5B, 0x65, 0x70)));
    private static readonly Pen AxisPen = new(Freeze(new SolidColorBrush(Color.FromRgb(0xDD, 0xE2, 0xE8))), 1);

    public IReadOnlyList<BenchmarkSeries>? Series
    {
        get => (IReadOnlyList<BenchmarkSeries>?)GetValue(SeriesProperty);
        set => SetValue(SeriesProperty, value);
    }

    public bool Percent
    {
        get => (bool)GetValue(PercentProperty);
        set => SetValue(PercentProperty, value);
    }

    protected override void OnRender(DrawingContext dc)
    {
        var width = ActualWidth;
        var height = ActualHeight;
        dc.DrawRectangle(Brushes.Transparent, null, new Rect(0, 0, width, height));
        var series = Series?.Where(s => s.Points.Count > 0).ToList();
        var dpi = VisualTreeHelper.GetDpi(this).PixelsPerDip;
        if (series is not { Count: > 0 } || width < 80 || height < 80)
        {
            Label(dc, "No data yet.", new Point(4, 4), dpi, TextBrush);
            return;
        }

        // Legend: a coloured dash and the name, wrapping across the top.
        double lx = 34, ly = 2;
        for (var i = 0; i < series.Count; i++)
        {
            var text = Text(series[i].Name, dpi, TextBrush);
            if (lx + 22 + text.Width > width) { lx = 34; ly += 16; }
            dc.DrawLine(new Pen(BrushOf(i, Series!, series[i]), 3), new Point(lx, ly + 8), new Point(lx + 14, ly + 8));
            dc.DrawText(text, new Point(lx + 18, ly));
            lx += 18 + text.Width + 16;
        }

        const double left = 34, bottom = 20;
        var top = ly + 24;
        var plotWidth = width - left - 8;
        var plotHeight = height - bottom - top;
        if (plotHeight < 30) return;
        var days = series.SelectMany(s => s.Points.Select(p => p.Day)).Distinct().OrderBy(d => d).ToList();
        var first = days[0];
        var span = Math.Max(1, (days[^1] - first).TotalDays);
        double X(DateTime day) => left + (days.Count == 1 ? plotWidth / 2 : plotWidth * (day - first).TotalDays / span);
        double Y(double v) => top + plotHeight * (1 - Math.Clamp(v, 0, 1));

        foreach (var tick in new[] { 0.0, 0.25, 0.5, 0.75, 1.0 })
        {
            dc.DrawLine(AxisPen, new Point(left, Y(tick)), new Point(width - 8, Y(tick)));
            Label(dc, Percent ? tick.ToString("P0", CultureInfo.CurrentCulture) : tick.ToString("0.00", CultureInfo.InvariantCulture),
                  new Point(0, Y(tick) - 7), dpi, TextBrush);
        }
        var labelEvery = Math.Max(1, (int)Math.Ceiling(days.Count / 8.0));
        for (var i = 0; i < days.Count; i += labelEvery)
            Label(dc, days[i].ToString("dd MMM", CultureInfo.CurrentCulture), new Point(X(days[i]) - 16, top + plotHeight + 3), dpi, TextBrush);

        for (var i = 0; i < series.Count; i++)
        {
            var brush = BrushOf(i, Series!, series[i]);
            var pen = new Pen(brush, 2);
            var points = series[i].Points.OrderBy(p => p.Day).Select(p => new Point(X(p.Day), Y(p.Value))).ToList();
            for (var j = 1; j < points.Count; j++) dc.DrawLine(pen, points[j - 1], points[j]);
            foreach (var p in points) dc.DrawEllipse(brush, null, p, 3, 3);
        }
    }

    /// <summary>The colour by the series' place in the full list, so a model keeps its colour on both charts.</summary>
    private static Brush BrushOf(int fallback, IReadOnlyList<BenchmarkSeries> all, BenchmarkSeries s)
    {
        var index = 0;
        for (; index < all.Count && all[index].Name != s.Name; index++) { }
        return Freeze(new SolidColorBrush(Palette[(index < all.Count ? index : fallback) % Palette.Length]));
    }

    private static FormattedText Text(string text, double dpi, Brush brush) =>
        new(text, CultureInfo.CurrentCulture, System.Windows.FlowDirection.LeftToRight, new Typeface("Segoe UI"), 11, brush, dpi);

    private static void Label(DrawingContext dc, string text, Point at, double dpi, Brush brush) =>
        dc.DrawText(new FormattedText(text, CultureInfo.CurrentCulture, System.Windows.FlowDirection.LeftToRight,
            new Typeface("Segoe UI"), 10, brush, dpi), at);

    private static T Freeze<T>(T freezable) where T : Freezable
    {
        freezable.Freeze();
        return freezable;
    }
}

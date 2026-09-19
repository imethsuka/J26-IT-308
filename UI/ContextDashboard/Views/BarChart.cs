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
/// Stacked hourly columns (saved over discarded), drawn directly: no charting library, so nothing
/// extra is loaded into memory while the window is open.
/// </summary>
public sealed class BarChart : FrameworkElement
{
    public static readonly DependencyProperty BucketsProperty = DependencyProperty.Register(
        nameof(Buckets), typeof(IReadOnlyList<HourBucket>), typeof(BarChart),
        new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender));

    private static readonly Brush SavedBrush = Freeze(new SolidColorBrush(Color.FromRgb(0x3F, 0x7D, 0x5E)));
    private static readonly Brush DiscardedBrush = Freeze(new SolidColorBrush(Color.FromRgb(0xE3, 0xE0, 0xD5)));
    private static readonly Brush TextBrush = Freeze(new SolidColorBrush(Color.FromRgb(0x5B, 0x65, 0x70)));
    private static readonly Pen AxisPen = new(Freeze(new SolidColorBrush(Color.FromRgb(0xDD, 0xE2, 0xE8))), 1);

    public IReadOnlyList<HourBucket>? Buckets
    {
        get => (IReadOnlyList<HourBucket>?)GetValue(BucketsProperty);
        set => SetValue(BucketsProperty, value);
    }

    protected override void OnRender(DrawingContext dc)
    {
        var width = ActualWidth;
        var height = ActualHeight;
        dc.DrawRectangle(Brushes.Transparent, null, new Rect(0, 0, width, height));
        var buckets = Buckets;
        if (buckets is not { Count: > 0 } || width < 40 || height < 40) return;

        const double left = 34, bottom = 20, top = 8;
        var plotWidth = width - left - 4;
        var plotHeight = height - bottom - top;
        var max = Math.Max(4, buckets.Max(b => b.Total));
        var step = plotWidth / buckets.Count;
        var dpi = VisualTreeHelper.GetDpi(this).PixelsPerDip;
        // Day buckets (7 days and up) are labelled by date, about eight labels across; hour buckets keep their HH:mm every 4th.
        var daily = buckets.Count > 1 && (buckets[1].HourStart - buckets[0].HourStart).TotalHours >= 20;
        var labelEvery = daily ? Math.Max(1, (int)Math.Ceiling(buckets.Count / 8.0)) : 4;

        dc.DrawLine(AxisPen, new Point(left, top + plotHeight), new Point(width - 4, top + plotHeight));
        foreach (var tick in new[] { 0.0, 0.5, 1.0 })
        {
            var y = top + plotHeight * (1 - tick);
            if (tick > 0) dc.DrawLine(AxisPen, new Point(left, y), new Point(width - 4, y));
            Label(dc, ((long)Math.Round(max * tick)).ToString("N0"), new Point(2, y - 7), dpi);
        }

        for (var i = 0; i < buckets.Count; i++)
        {
            var b = buckets[i];
            var x = left + i * step + step * 0.15;
            var barWidth = step * 0.7;
            var savedHeight = plotHeight * b.Saved / max;
            var discardedHeight = plotHeight * b.Discarded / max;
            var baseline = top + plotHeight;
            if (savedHeight > 0) dc.DrawRectangle(SavedBrush, null, new Rect(x, baseline - savedHeight, barWidth, savedHeight));
            if (discardedHeight > 0) dc.DrawRectangle(DiscardedBrush, null, new Rect(x, baseline - savedHeight - discardedHeight, barWidth, discardedHeight));
            if (i % labelEvery == 0) Label(dc, b.HourStart.ToString(daily ? "dd MMM" : "HH:mm", CultureInfo.CurrentCulture), new Point(x - 6, baseline + 3), dpi);
        }
    }

    private static void Label(DrawingContext dc, string text, Point at, double dpi) =>
        dc.DrawText(new FormattedText(text, CultureInfo.CurrentCulture, System.Windows.FlowDirection.LeftToRight,
            new Typeface("Segoe UI"), 10, TextBrush, dpi), at);

    private static T Freeze<T>(T freezable) where T : Freezable
    {
        freezable.Freeze();
        return freezable;
    }
}

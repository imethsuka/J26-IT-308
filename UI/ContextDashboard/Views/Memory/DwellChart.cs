using System.Globalization;
using System.Windows;
using System.Windows.Media;
using ContextDashboard.Data;
using Brush = System.Windows.Media.Brush;
using Brushes = System.Windows.Media.Brushes;
using Color = System.Windows.Media.Color;
using Point = System.Windows.Point;

namespace ContextDashboard.Views;

/// <summary>Soft rounded columns, one per dwell bucket: the discarded part grey, the saved part green on top of it.</summary>
public sealed class DwellChart : FrameworkElement
{
    public static readonly DependencyProperty BucketsProperty = DependencyProperty.Register(
        nameof(Buckets), typeof(IReadOnlyList<DwellBucket>), typeof(DwellChart),
        new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender));

    private static readonly Brush Saved = Freeze(new SolidColorBrush(Color.FromRgb(0x3F, 0x7D, 0x5E)));
    private static readonly Brush Discarded = Freeze(new SolidColorBrush(Color.FromRgb(0xE8, 0xE5, 0xDB)));
    private static readonly Brush Muted = Freeze(new SolidColorBrush(Color.FromRgb(0x7A, 0x7F, 0x78)));
    private static readonly Brush Ink = Freeze(new SolidColorBrush(Color.FromRgb(0x1F, 0x24, 0x21)));

    public IReadOnlyList<DwellBucket>? Buckets
    {
        get => (IReadOnlyList<DwellBucket>?)GetValue(BucketsProperty);
        set => SetValue(BucketsProperty, value);
    }

    protected override void OnRender(DrawingContext dc)
    {
        dc.DrawRectangle(Brushes.Transparent, null, new Rect(0, 0, ActualWidth, ActualHeight));
        if (Buckets is not { Count: > 0 } buckets || ActualWidth < 60 || ActualHeight < 80) return;

        const double labelBand = 44;
        var plotHeight = ActualHeight - labelBand;
        var max = Math.Max(1, buckets.Max(b => b.Total));
        var slot = ActualWidth / buckets.Count;
        var barWidth = Math.Min(54, slot * 0.46);

        for (var i = 0; i < buckets.Count; i++)
        {
            var b = buckets[i];
            var x = i * slot + (slot - barWidth) / 2;
            var total = b.Total == 0 ? 0 : Math.Max(4, plotHeight * b.Total / max);
            var top = plotHeight - total;

            // The full column in grey, then the saved share painted over its lower part.
            if (total > 0) dc.DrawRoundedRectangle(Discarded, null, new Rect(x, top, barWidth, total), 7, 7);
            if (b.Saved > 0)
            {
                var savedHeight = Math.Max(4, total * b.Saved / b.Total);
                var savedRect = new Rect(x, plotHeight - savedHeight, barWidth, savedHeight);
                dc.DrawRoundedRectangle(Saved, null, savedRect, 7, 7);
                if (savedHeight < total) dc.DrawRectangle(Saved, null, new Rect(x, savedRect.Top, barWidth, Math.Min(10, savedHeight)));
            }

            Text(dc, b.Label, Muted, 11.5, x + barWidth / 2, plotHeight + 8);
            Text(dc, b.Total.ToString("N0", CultureInfo.CurrentCulture), Ink, 12.5, x + barWidth / 2, plotHeight + 25);
        }
    }

    private void Text(DrawingContext dc, string text, Brush brush, double size, double centerX, double y)
    {
        var ft = new FormattedText(text, CultureInfo.CurrentUICulture, System.Windows.FlowDirection.LeftToRight,
            new Typeface("Consolas"), size, brush, VisualTreeHelper.GetDpi(this).PixelsPerDip);
        dc.DrawText(ft, new Point(centerX - ft.Width / 2, y));
    }

    private static Brush Freeze(Brush b)
    {
        b.Freeze();
        return b;
    }
}

using System.Collections.Concurrent;
using System.Text.Json;
using System.Windows;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using System.Windows.Threading;
using ContextDashboard.Data;
using ContextDashboard.Services;
using Microsoft.Data.Sqlite;
using Brush = System.Windows.Media.Brush;
using Canvas = System.Windows.Controls.Canvas;
using DatePicker = System.Windows.Controls.DatePicker;
using Rectangle = System.Windows.Shapes.Rectangle;
using Color = System.Windows.Media.Color;
using ColorConverter = System.Windows.Media.ColorConverter;
using FontFamily = System.Windows.Media.FontFamily;
using KeyEventArgs = System.Windows.Input.KeyEventArgs;

namespace ContextDashboard.Views;

/// <summary>
/// The history slider: one local day of stored context records on a time slider. For the moment under the thumb it shows
/// the screenshot kept for it (when screenshots are on and the importance model saved that moment) and the stored page with
/// what was on screen highlighted. Images and text are decrypted in memory for display only.
/// </summary>
public partial class HistoryPage : UserControl
{
    private const double JumpSeconds = 600;

    private MainViewModel? _vm;
    private HistoryDb? _db;
    private HistoryDay? _day;
    private IReadOnlyList<DateOnly> _days = [];
    private readonly ConcurrentDictionary<string, StoredPage?> _pages = new();
    private readonly DispatcherTimer _debounce = new() { Interval = TimeSpan.FromMilliseconds(120) };
    private int _shown = -1;
    private int _version;
    private bool _quiet; // set while the code moves the slider or the date picker itself

    public HistoryPage()
    {
        InitializeComponent();
        _debounce.Tick += (_, _) =>
        {
            _debounce.Stop();
            if (_day is { Points.Count: > 0 } day) Show(day.IndexAt(SecondsToUs(TimeSlider.Value)));
        };
        Loaded += async (_, _) =>
        {
            if (_vm is not null) return;
            _vm = (MainViewModel)DataContext;
            var s = _vm.Settings;
            _db = new HistoryDb(s.DbPath, s.MemoryPath, s.ScreenshotsPath, s.RawBufferPath);
            ShotsBox.IsChecked = _vm.ScreenshotsActive;
            try
            {
                var db = _db;
                _days = await Task.Run(db.Days);
            }
            catch (Exception ex) when (ex is SqliteException or IOException)
            {
                StatusText.Text = "Could not read the history: " + ex.Message;
            }
            var today = DateOnly.FromDateTime(DateTime.Now);
            await LoadDayAsync(_days.Count == 0 || _days.Contains(today) ? today : _days[^1]);
        };
    }

    // ---- days --------------------------------------------------------------------------------------------------------

    private async Task LoadDayAsync(DateOnly day)
    {
        if (_db is null) return;
        _quiet = true;
        DayPicker.SelectedDate = day.ToDateTime(TimeOnly.MinValue);
        _quiet = false;
        var version = ++_version;
        HistoryDay loaded;
        ScreenshotStats shots;
        try
        {
            var db = _db;
            (loaded, shots) = await Task.Run(() => (db.LoadDay(day), db.Screenshots()));
        }
        catch (Exception ex) when (ex is SqliteException or IOException)
        {
            StatusText.Text = "Could not read this day: " + ex.Message;
            return;
        }
        if (version != _version) return;

        _day = loaded;
        _pages.Clear();
        _shown = -1;
        var points = loaded.Points;
        var stored = shots.Shots == 0 ? "no screenshots stored" : $"{shots.Shots:N0} screenshots stored ({shots.Bytes / 1048576.0:N0} MB)";
        DayText.Text = points.Count == 0
            ? $"Nothing recorded on this day.  ·  {stored}"
            : $"{points.Count:N0} moments  ·  {loaded.ShotCount:N0} with a screenshot  ·  {loaded.MomentCount:N0} with page text  ·  {stored}";

        if (points.Count == 0)
        {
            TimeSlider.IsEnabled = false;
            StartLabel.Text = EndLabel.Text = NowLabel.Text = "";
            Strip.Children.Clear();
            ShowNothing("Nothing was recorded on this day.");
            return;
        }
        TimeSlider.IsEnabled = true;
        _quiet = true;
        TimeSlider.Minimum = UsToSeconds(points[0].TsUs);
        TimeSlider.Maximum = Math.Max(TimeSlider.Minimum + 1, UsToSeconds(points[^1].TsUs));
        TimeSlider.SmallChange = 1;
        TimeSlider.LargeChange = JumpSeconds;
        TimeSlider.Value = TimeSlider.Maximum; // start at the latest moment, like scrolling back from now
        _quiet = false;
        StartLabel.Text = points[0].Time.ToString("HH:mm");
        EndLabel.Text = points[^1].Time.ToString("HH:mm");
        DrawStrip();
        Show(points.Count - 1);
    }

    private async void OnDayPicked(object? sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (_quiet || DayPicker.SelectedDate is not { } picked) return;
        await LoadDayAsync(DateOnly.FromDateTime(picked));
    }

    private async void OnPrevDay(object sender, RoutedEventArgs e)
    {
        if (_day is null) return;
        var earlier = _days.LastOrDefault(d => d < _day.Day);
        if (earlier != default) await LoadDayAsync(earlier);
    }

    private async void OnNextDay(object sender, RoutedEventArgs e)
    {
        if (_day is null) return;
        var later = _days.FirstOrDefault(d => d > _day.Day);
        if (later != default) await LoadDayAsync(later);
    }

    private async void OnToday(object sender, RoutedEventArgs e) => await LoadDayAsync(DateOnly.FromDateTime(DateTime.Now));

    // ---- the slider --------------------------------------------------------------------------------------------------

    private void OnSliderMoved(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (_quiet || _day is null) return;
        NowLabel.Text = DateTimeOffset.FromUnixTimeMilliseconds((long)(TimeSlider.Value * 1000)).ToLocalTime().ToString("HH:mm:ss");
        _debounce.Stop();
        _debounce.Start(); // decrypting an image and a page on every pixel of a drag would lag
    }

    private void OnKey(object sender, KeyEventArgs e)
    {
        if (_day is not { Points.Count: > 0 } day || e.OriginalSource is DatePicker or System.Windows.Controls.Primitives.DatePickerTextBox) return;
        var index = _shown < 0 ? day.Points.Count - 1 : _shown;
        int? target = e.Key switch
        {
            Key.Left => Math.Max(0, index - 1),
            Key.Right => Math.Min(day.Points.Count - 1, index + 1),
            Key.PageUp => day.IndexAt(day.Points[index].TsUs - SecondsToUs(JumpSeconds)),
            Key.PageDown => day.IndexAt(day.Points[index].TsUs + SecondsToUs(JumpSeconds)),
            _ => null,
        };
        if (target is not { } t) return;
        e.Handled = true;
        _quiet = true;
        TimeSlider.Value = UsToSeconds(day.Points[t].TsUs);
        _quiet = false;
        Show(t);
    }

    private void OnStripResized(object sender, SizeChangedEventArgs e) => DrawStrip();

    /// <summary>One band per stretch of the same app, placed by time like the slider.</summary>
    private void DrawStrip()
    {
        Strip.Children.Clear();
        if (_day is not { Points.Count: > 0 } day || Strip.ActualWidth <= 0) return;
        var points = day.Points;
        double first = points[0].TsUs, span = Math.Max(1, points[^1].TsUs - first), width = Strip.ActualWidth;
        var start = 0;
        for (var i = 1; i <= points.Count; i++)
        {
            if (i < points.Count && string.Equals(points[i].App, points[start].App, StringComparison.OrdinalIgnoreCase)) continue;
            var x0 = (points[start].TsUs - first) / span * width;
            var x1 = i < points.Count ? (points[i].TsUs - first) / span * width : width;
            var band = new Rectangle { Width = Math.Max(1, x1 - x0), Height = Strip.Height, Fill = AppBrush(points[start].App), ToolTip = points[start].App };
            Canvas.SetLeft(band, x0);
            Strip.Children.Add(band);
            start = i;
        }
    }

    private static readonly Brush[] Palette =
        new[] { "#7C6BB5", "#3F7D5E", "#C48A3A", "#B85C5C", "#4F7CA8", "#8C6D4F", "#5E9C9A", "#A0679B" }
            .Select(c => (Brush)new SolidColorBrush((Color)ColorConverter.ConvertFromString(c))).ToArray();

    private static Brush AppBrush(string app)
    {
        var hash = 17;
        foreach (var ch in app.ToLowerInvariant()) hash = unchecked(hash * 31 + ch); // stable across runs, unlike GetHashCode
        return Palette[(hash & 0x7fffffff) % Palette.Length];
    }

    // ---- one moment --------------------------------------------------------------------------------------------------

    private async void Show(int index)
    {
        if (_day is not { } day || _db is not { } db || index == _shown || index < 0 || index >= day.Points.Count) return;
        _shown = index;
        var version = ++_version;
        var point = day.Points[index];
        NowLabel.Text = point.Time.ToString("HH:mm:ss");
        WhenText.Text = point.Time.ToString("dddd d MMMM, HH:mm:ss");
        TitleText.Text = point.Title.Length > 0 ? point.Title : "(no title)";
        WhereText.Text = string.Join("  ·  ", new[] { point.App, point.Site, point.Saved ? "saved" : "not saved" }.Where(p => p.Length > 0));

        var shot = day.ShotFor(index);
        var moment = day.MomentFor(index);
        var waiting = day.TextWaiting(index);
        BitmapImage? image = null;
        StoredPage? page = null;
        string? problem = null;
        try
        {
            (image, page) = await Task.Run(() => (shot is null ? null : Decode(db.ShotJpeg(shot.Id)),
                moment is null ? null : _pages.GetOrAdd(moment.DocId, db.Page)));
        }
        catch (Exception ex) when (ex is SqliteException or IOException or JsonException or NotSupportedException or InvalidDataException)
        {
            problem = ex.Message;
        }
        if (version != _version) return; // the user has moved on

        ShowShot(point, shot, image);
        ShowPage(moment, page, waiting);
        StatusText.Text = problem is null ? "" : "Could not open this moment: " + problem;
    }

    private static BitmapImage? Decode(byte[]? jpeg)
    {
        if (jpeg is null) return null;
        var image = new BitmapImage();
        image.BeginInit();
        image.CacheOption = BitmapCacheOption.OnLoad;
        image.StreamSource = new MemoryStream(jpeg);
        image.EndInit();
        image.Freeze(); // built off the UI thread
        return image;
    }

    private void ShowShot(HistoryPoint point, HistoryShot? shot, BitmapImage? image)
    {
        ShotImage.Source = image;
        ShotBadge.Visibility = image is null ? Visibility.Collapsed : Visibility.Visible;
        if (image is not null && shot is not null)
        {
            var at = ContextDb.FromEpochUs(shot.TsUs).ToLocalTime();
            ShotTime.Text = shot.TsUs == point.TsUs ? at.ToString("HH:mm:ss") : $"{at:HH:mm:ss}  ·  same window, earlier";
            ShotPlaceholder.Text = "";
            return;
        }
        ShotPlaceholder.Text = _vm?.ScreenshotsActive != true && _day?.ShotCount == 0
            ? "Screenshots are off. Turn on “Keep screenshots” above to see your screen here for the moments the importance model saves."
            : point.Saved
                ? "No screenshot for this moment."
                : "No screenshot: the importance model did not save this moment, or anything earlier in the same window.";
    }

    private void ShowPage(HistoryMoment? moment, StoredPage? page, bool waiting)
    {
        if (moment is null || page is null)
        {
            PageView.Document = null;
            TextNote.Text = waiting
                ? "This moment's text is still in the 3-day buffer. It appears here after the nightly job (PC idle and plugged in) has filtered it."
                : "No page text was kept for this moment: screen text was off, the moment was of low importance, or no privacy filter was running.";
            return;
        }
        var excerpt = HistoryDb.Excerpt(page, moment);
        TextNote.Text = excerpt.Highlight.Length > 0
            ? "The highlighted part was on screen. The rest is the same page as you scrolled it, privacy-filtered."
            : "This moment could not be placed in the stored page (its text changed since), so the page is shown from the top.";

        var ink = (Brush)FindResource("Ink");
        var muted = (Brush)FindResource("Muted");
        var paragraph = new Paragraph { Margin = new Thickness(0) };
        if (excerpt.CutBefore) paragraph.Inlines.Add(new Run("…\n") { Foreground = muted });
        paragraph.Inlines.Add(new Run(excerpt.Before));
        var highlight = new Run(excerpt.Highlight) { Background = (Brush)FindResource("PurpleSoft") };
        paragraph.Inlines.Add(highlight);
        paragraph.Inlines.Add(new Run(excerpt.After));
        if (excerpt.CutAfter) paragraph.Inlines.Add(new Run("\n…") { Foreground = muted });
        PageView.Document = new FlowDocument(paragraph)
        {
            FontFamily = new FontFamily("Segoe UI"), FontSize = 13, Foreground = ink, PagePadding = new Thickness(0, 0, 8, 0),
        };
        if (excerpt.Highlight.Length > 0)
            Dispatcher.BeginInvoke(DispatcherPriority.Loaded, highlight.BringIntoView);
    }

    private void ShowNothing(string why)
    {
        ShotImage.Source = null;
        ShotBadge.Visibility = Visibility.Collapsed;
        ShotPlaceholder.Text = why;
        PageView.Document = null;
        WhenText.Text = TitleText.Text = WhereText.Text = TextNote.Text = "";
    }

    // ---- screenshots on / off ----------------------------------------------------------------------------------------

    private async void OnShotsClicked(object sender, RoutedEventArgs e)
    {
        if (_vm is null) return;
        var wantOn = ShotsBox.IsChecked == true;
        if (wantOn)
        {
            var answer = System.Windows.MessageBox.Show(
                Window.GetWindow(this), AppSettings.ScreenshotsConsentText + "\n\nKeep screenshots?", "Screenshots",
                MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.No);
            if (answer != MessageBoxResult.Yes)
            {
                ShotsBox.IsChecked = false;
                return;
            }
        }
        StatusText.Text = await _vm.SetScreenshotsAsync(wantOn);
        ShotsBox.IsChecked = _vm.ScreenshotsActive;
    }

    private async void OnDeleteShots(object sender, RoutedEventArgs e)
    {
        if (_db is null) return;
        var answer = System.Windows.MessageBox.Show(
            Window.GetWindow(this), "Delete every stored screenshot? This cannot be undone.", "Delete screenshots",
            MessageBoxButton.YesNo, MessageBoxImage.Warning, MessageBoxResult.No);
        if (answer != MessageBoxResult.Yes) return;
        try
        {
            var db = _db;
            var deleted = await Task.Run(db.DeleteAllScreenshots);
            StatusText.Text = $"Deleted {deleted:N0} screenshots.";
        }
        catch (Exception ex) when (ex is SqliteException or IOException)
        {
            StatusText.Text = "Could not delete the screenshots right now (" + ex.Message + "). Try again, or stop capture first.";
        }
        if (_day is not null) await LoadDayAsync(_day.Day);
    }

    private static long SecondsToUs(double seconds) => (long)(seconds * 1_000_000);

    private static double UsToSeconds(long us) => us / 1_000_000.0;
}

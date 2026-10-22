using System.Collections.ObjectModel;
using ContextDashboard.Data;
using ContextDashboard.Services;

namespace ContextDashboard.Views;

/// <summary>The Model benchmark page: shadow-mode results (<see cref="ContextDb.Benchmark"/>) and the Make champion button.</summary>
public sealed partial class MainViewModel
{
    private ChampionSwitch? _championSwitch;
    private ChampionSwitch Champions => _championSwitch ??= new ChampionSwitch(Settings);

    private BenchmarkData _benchmark = BenchmarkData.Empty;
    private string _benchmarkStatus = "", _benchmarkSummary = "";
    private bool _benchmarkBusy;

    public ObservableCollection<BenchmarkRow> BenchmarkRows { get; } = [];
    public ObservableCollection<ChampionChange> ChampionChanges { get; } = [];
    public IReadOnlyList<BenchmarkSeries> BenchmarkKeepRate => _benchmark.KeepRateByDay;
    public IReadOnlyList<BenchmarkSeries> BenchmarkAp => _benchmark.ApByDay;
    public string BenchmarkStatus { get => _benchmarkStatus; private set => Set(ref _benchmarkStatus, value); }
    public string BenchmarkSummary { get => _benchmarkSummary; private set => Set(ref _benchmarkSummary, value); }
    public bool BenchmarkIdle => !_benchmarkBusy;
    public bool BenchmarkVisible { get; set; }

    private string _retentionText = "";
    public string RetentionText { get => _retentionText; private set => Set(ref _retentionText, value); }

    /// <summary>Saves the keep times (clamped to their ranges, or the defaults) and restarts capture so the worker uses them.</summary>
    public async Task ApplyRetentionAsync(bool defaults = false)
    {
        var s = Settings;
        if (defaults)
            (s.ScreenTextKeepDays, s.ScreenshotsKeepDays, s.ScreenshotsMaxMb, s.ScreenshotsQuality) =
                (AppSettings.DefaultScreenTextKeepDays, AppSettings.DefaultScreenshotsKeepDays, AppSettings.DefaultScreenshotsMaxMb,
                 AppSettings.DefaultScreenshotsQuality);
        (s.ScreenTextKeepDays, s.ScreenshotsKeepDays, s.ScreenshotsMaxMb, s.ScreenshotsQuality) =
            (s.ScreenTextKeepDaysEffective, s.ScreenshotsKeepDaysEffective, s.ScreenshotsMaxMbEffective, s.ScreenshotsQualityEffective);
        s.Save();
        Notify(nameof(Settings));
        NotifyScreenText();
        var what = $"Screen text is kept {s.ScreenTextKeepDays} days; screenshots {s.ScreenshotsKeepDays} days, up to {s.ScreenshotsMaxMb:N0} MB, "
                   + $"at {s.ScreenshotsQuality}% quality.";
        if (WorkerRunning)
        {
            RetentionText = what + " Restarting capture to apply it…";
            await RestartCaptureAsync();
            RetentionText = what + " Applied.";
        }
        else
        {
            RetentionText = what + " It applies when capture starts.";
        }
    }

    /// <summary>Reads the benchmark (heavier than the 3 s refresh, so only while the page is open and on Refresh).</summary>
    public async Task RefreshBenchmarkAsync()
    {
        if (_benchmarkBusy) return;
        _benchmarkBusy = true;
        Notify(nameof(BenchmarkIdle));
        try
        {
            var data = await Task.Run(() => _db.Benchmark());
            _benchmark = data;
            Replace(BenchmarkRows, data.Rows);
            Replace(ChampionChanges, data.Changes);
            Notify(nameof(BenchmarkKeepRate));
            Notify(nameof(BenchmarkAp));
            BenchmarkSummary = !data.Available
                ? "No shadow-mode data yet. It starts when capture runs with this build; the first live grades follow 48 hours later, when those screens get their labels."
                : $"Recording since {data.FirstDecision:dd MMM HH:mm}. {data.GradedScreens:N0} screens graded so far"
                  + (data.BaseRate is { } rate ? $" ({rate:P0} important, so a model that guesses scores an AP of {rate:0.00})." : ".");
        }
        catch (Exception ex)
        {
            BenchmarkSummary = $"Could not read the benchmark: {ex.Message}";
        }
        finally
        {
            _benchmarkBusy = false;
            Notify(nameof(BenchmarkIdle));
        }
    }

    /// <summary>Makes <paramref name="row"/>'s newest version the champion, then restarts capture so the worker uses it.</summary>
    public async Task MakeChampionAsync(BenchmarkRow row)
    {
        if (row.LatestVersion is null || _benchmarkBusy) return;
        _benchmarkBusy = true;
        Notify(nameof(BenchmarkIdle));
        try
        {
            BenchmarkStatus = $"Making {row.Display} the champion…";
            var result = await Champions.SetAsync(row.LatestVersion, $"manual (dashboard): {row.Display}, live AP {row.ApText}");
            BenchmarkStatus = result.Message;
            if (result.Ok && result.Changed && WorkerRunning)
            {
                BenchmarkStatus = result.Message + " Restarting capture…";
                await RestartCaptureAsync();
                BenchmarkStatus = result.Message;
            }
        }
        catch (Exception ex)
        {
            BenchmarkStatus = $"The champion was not changed: {ex.Message}";
        }
        finally
        {
            _benchmarkBusy = false;
            Notify(nameof(BenchmarkIdle));
        }
        await RefreshBenchmarkAsync();
    }
}

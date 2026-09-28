using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using ContextDashboard.Data;
using ContextDashboard.Services;
using Microsoft.Data.Sqlite;

namespace ContextDashboard.Views;

public sealed record AppBar(string App, long Records, long Saved, double Share);

/// <summary>
/// What the window shows. Reads happen off the UI thread, and only while the window exists: there is
/// no background work in this process once it is closed.
/// </summary>
public sealed class MainViewModel : INotifyPropertyChanged
{
    private readonly ContextDb _db;
    private readonly ServiceControl _services;
    private readonly LearningRunner _learning;
    private readonly ScreenTextDb _screen;
    private int _refreshing;

    public MainViewModel(AppSettings settings)
    {
        Settings = settings;
        _db = new ContextDb(settings.DbPath);
        _services = new ServiceControl(settings);
        _learning = new LearningRunner(settings);
        _screen = new ScreenTextDb(settings.RawBufferPath, settings.MemoryPath);
        Writer = new MemoryWriter(settings);
    }

    // ---- Screen text (opt-in) ----------------------------------------------------------------
    private string _rawText = "", _memoryText = "", _screenStatus = "";
    private bool _screenBusy;

    public ObservableCollection<MemoryRow> MemoryEntries { get; } = [];
    public bool ScreenTextActive => Settings.ScreenTextActive;
    public bool KeepUnredacted => Settings.ScreenTextKeepUnredacted;
    public bool ScreenIdle => !_screenBusy;
    public string ScreenStateText => Settings.ScreenTextActive
        ? "On. The words visible in your active window are being captured, encrypted, for up to 3 days."
        : "Off. Nothing on your screen is read as text.";
    public string FilterText => !Settings.ScreenTextActive ? ""
        : Settings.ScreenTextKeepUnredacted
            ? "Developer mode: no privacy filter, so memory entries are built from unredacted text."
            : "No privacy filter is installed yet, so memory entries hold only the app, site, title and counts. The captured text is deleted after 3 days.";
    public string RawBufferText { get => _rawText; private set => Set(ref _rawText, value); }
    public string MemoryStatsText { get => _memoryText; private set => Set(ref _memoryText, value); }
    public string ScreenStatus { get => _screenStatus; private set => Set(ref _screenStatus, value); }

    private void NotifyScreenText()
    {
        Notify(nameof(ScreenTextActive));
        Notify(nameof(KeepUnredacted));
        Notify(nameof(ScreenStateText));
        Notify(nameof(FilterText));
    }

    /// <summary>Opt in (the caller has shown the consent text and the user agreed) or out. Capture restarts to apply it.</summary>
    public async Task SetScreenTextAsync(bool on)
    {
        if (on) Settings.OptInScreenText(DateTimeOffset.Now);
        else Settings.OptOutScreenText();
        Settings.Save();
        NotifyScreenText();
        await ApplyScreenTextChangeAsync(on ? "Screen text is on." : "Screen text is off.");
    }

    public async Task SetKeepUnredactedAsync(bool on)
    {
        Settings.ScreenTextKeepUnredacted = on && Settings.ScreenTextActive;
        Settings.Save();
        NotifyScreenText();
        await ApplyScreenTextChangeAsync(on ? "Developer mode is on." : "Developer mode is off.");
    }

    private async Task ApplyScreenTextChangeAsync(string what)
    {
        if (CollectorRunning || WorkerRunning)
        {
            ScreenStatus = what + " Restarting capture to apply it…";
            await RestartCaptureAsync();
            ScreenStatus = what;
        }
        else
        {
            ScreenStatus = what + " It applies the next time capture starts.";
        }
    }

    /// <summary>Deletes every captured text and memory entry. Capture keeps running; new text starts from zero.</summary>
    public async Task DeleteScreenTextAsync()
    {
        _screenBusy = true;
        Notify(nameof(ScreenIdle));
        try
        {
            var (captures, entries) = await Task.Run(_screen.DeleteAll);
            ScreenStatus = $"Deleted {captures:N0} captured texts and {entries:N0} memory entries.";
        }
        catch (Exception ex) when (ex is SqliteException or IOException)
        {
            ScreenStatus = "Could not delete everything right now (" + ex.Message + "). Try again, or stop capture first.";
        }
        finally
        {
            _screenBusy = false;
            Notify(nameof(ScreenIdle));
        }
        await RefreshAsync();
    }

    // ---- Model 2: learning -----------------------------------------------------------------
    private string _learningText = "", _learningStatus = "", _learnerText = "", _restartText = "";
    private bool _learningBusy;

    /// <summary>The newest learning run, as the job logged it.</summary>
    public string LearningText { get => _learningText; private set => Set(ref _learningText, value); }
    /// <summary>Progress while a run is in flight.</summary>
    public string LearningStatus { get => _learningStatus; private set => Set(ref _learningStatus, value); }
    public string LearnerText { get => _learnerText; private set => Set(ref _learnerText, value); }
    public string RestartText { get => _restartText; private set => Set(ref _restartText, value); }
    public bool LearningBusy { get => _learningBusy; private set { Set(ref _learningBusy, value); Notify(nameof(LearningIdle)); } }
    public bool LearningIdle => !_learningBusy;
    public bool QuickTest { get; set; }
    public bool UseKw { get; set; }

    /// <summary>Runs one full learning cycle now: back up, label, learn prototypes, retrain, apply the deploy gate.</summary>
    public async Task RunLearningAsync()
    {
        if (LearningBusy) return;
        LearningBusy = true;
        LearningStatus = "Backing up the database and running one learning cycle…";
        try
        {
            var result = await _learning.RunOnceAsync(QuickTest, UseKw ? "kw" : "v3");
            LearningText = result.Summary;
            LearningStatus = result.Error is null ? _learning.CapabilityNote ?? "" : $"The run failed: {result.Error} (see the logs folder).";
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException or TimeoutException or System.ComponentModel.Win32Exception)
        {
            LearningStatus = ex.Message;
        }
        finally
        {
            LearningBusy = false;
        }
        await RefreshAsync();
    }

    /// <summary>The worker loads its model at startup only, so a newly deployed model needs a restart.</summary>
    public async Task RestartCaptureAsync()
    {
        StatusMessage = "Restarting capture…";
        await Task.Run(_services.Stop);
        await StartAsync();
    }

    public AppSettings Settings { get; }
    public ObservableCollection<RecordRow> Recent { get; } = [];
    public ObservableCollection<AppBar> TopApps { get; } = [];
    public ObservableCollection<ModelRow> Models { get; } = [];

    public string FeedFilter { get; set; } = "";
    public string FeedDecision { get; set; } = "all";

    private bool _collectorRunning, _workerRunning;
    public bool CollectorRunning { get => _collectorRunning; private set => Set(ref _collectorRunning, value); }
    public bool WorkerRunning { get => _workerRunning; private set => Set(ref _workerRunning, value); }

    private string _records = "–", _saveRate = "–", _embedding = "–", _lastCapture = "–", _disk = "–";
    private string _activeModel = "–", _drc = "", _warning = "", _status = "";
    private IReadOnlyList<HourBucket> _hours = [];

    public string RecordsText { get => _records; private set => Set(ref _records, value); }
    public string SaveRateText { get => _saveRate; private set => Set(ref _saveRate, value); }
    public string EmbeddingText { get => _embedding; private set => Set(ref _embedding, value); }
    public string LastCaptureText { get => _lastCapture; private set => Set(ref _lastCapture, value); }
    public string DiskText { get => _disk; private set => Set(ref _disk, value); }
    public string ActiveModelText { get => _activeModel; private set => Set(ref _activeModel, value); }
    public string DrcText { get => _drc; private set => Set(ref _drc, value); }
    public string Warning { get => _warning; private set => Set(ref _warning, value); }
    public string StatusMessage { get => _status; set => Set(ref _status, value); }
    /// <summary>How far back the activity chart goes: 1 = last 24 hours (hourly bars), more = that many days (daily bars).</summary>
    public int ActivityDays { get; set; } = 1;
    private string _activityTitle = "Activity, last 24 hours";
    public string ActivityTitle { get => _activityTitle; private set => Set(ref _activityTitle, value); }
    public IReadOnlyList<HourBucket> Hours { get => _hours; private set => Set(ref _hours, value); }

    // ---- Importance Engine page --------------------------------------------------------------
    private EngineStats _engine = EngineStats.Empty;
    private int _engineRefreshing;

    public EngineStats Engine { get => _engine; private set => Set(ref _engine, value); }

    /// <summary>True while the engine page is on screen: its query parses a week of feature JSON, so it only runs then.</summary>
    public bool EngineVisible { get; set; }

    /// <summary>Which records the engine page covers: all, live capture only, or the merged DRC history.</summary>
    public DataScope EngineScope { get; set; } = DataScope.All;

    /// <summary>Which records the live feed lists.</summary>
    public DataScope FeedScope { get; set; } = DataScope.All;

    public string LiveSourceText => Sources.LiveCount == 0 ? "No live records yet"
        : $"{Sources.LiveCount:N0} records, {Span(Sources.LiveFrom, Sources.LiveTo)}";
    public string HistorySourceText => Sources.HasHistory
        ? $"{Sources.HistoryCount:N0} records, {Span(Sources.HistoryFrom, Sources.HistoryTo)}"
        : "None merged yet (merge_drc_into_live.py)";

    private static string Span(DateTimeOffset? from, DateTimeOffset? to) =>
        from is { } a && to is { } b ? $"{a.ToLocalTime():dd MMM} to {b.ToLocalTime():dd MMM HH:mm}" : "";

    /// <summary>What the learning job wrote about sessions and days, read with each refresh.</summary>
    public IReadOnlyDictionary<string, SessionNote> SessionNotes { get; private set; } = new Dictionary<string, SessionNote>();
    public IReadOnlyDictionary<string, DayNote> DayNotes { get; private set; } = new Dictionary<string, DayNote>();

    // ---- "Write summaries now" ---------------------------------------------------------------------------------
    private string _waitingText = "", _writerStatus = "";
    private bool _writerRunning;
    private double _writerValue;
    private bool _writerIndeterminate = true;

    public MemoryWriter Writer { get; }
    public string WaitingText { get => _waitingText; private set => Set(ref _waitingText, value); }
    public string WriterStatus { get => _writerStatus; set => Set(ref _writerStatus, value); }
    public bool WriterRunning { get => _writerRunning; private set { Set(ref _writerRunning, value); Notify(nameof(WriterIdle)); } }
    public bool WriterIdle => !_writerRunning;
    public double WriterValue { get => _writerValue; private set => Set(ref _writerValue, value); }
    public bool WriterIndeterminate { get => _writerIndeterminate; private set => Set(ref _writerIndeterminate, value); }

    /// <summary>Writes sentences for the next waiting memories, then refreshes session notes and day recaps. Runs in the background.</summary>
    public async Task WriteSummariesAsync()
    {
        if (WriterRunning) return;
        if (Writer.Problem() is { } problem)
        {
            WriterStatus = problem;
            return;
        }
        WriterRunning = true;
        WriterIndeterminate = true;
        WriterStatus = "Loading the language model\u2026";
        try
        {
            WriterStatus = await Writer.RunAsync(MemoryWriter.DefaultEntries, p =>
            {
                WriterIndeterminate = p.Total == 0;
                WriterValue = p.Total == 0 ? 0 : 100.0 * p.Done / p.Total;
                WriterStatus = p.Stage == "summaries"
                    ? $"Writing sentences: {p.Done} of {p.Total}"
                    : "Writing session notes and day recaps\u2026";
            });
        }
        finally
        {
            WriterRunning = false;
        }
        await RefreshAsync();
    }

    public void CancelWriter() => Writer.Cancel();

    private DataSources _sources = DataSources.None;
    public DataSources Sources
    {
        get => _sources;
        private set
        {
            if (!Set(ref _sources, value)) return;
            Notify(nameof(LiveSourceText));
            Notify(nameof(HistorySourceText));
        }
    }

    public async Task RefreshEngineAsync()
    {
        if (Interlocked.Exchange(ref _engineRefreshing, 1) == 1) return;
        try
        {
            var scope = EngineScope;
            Engine = await Task.Run(() => _db.Engine(null, scope));
        }
        catch (Exception ex) when (ex is SqliteException or IOException)
        {
            // Worker mid-checkpoint: keep the last numbers, try again next tick.
        }
        finally
        {
            Interlocked.Exchange(ref _engineRefreshing, 0);
        }
    }

    public async Task RefreshAsync()
    {
        if (Interlocked.Exchange(ref _refreshing, 1) == 1) return;
        try
        {
            var filter = FeedFilter;
            var decision = FeedDecision;
            var feedScope = FeedScope;
            var activityDays = ActivityDays;
            var snapshot = await Task.Run(() => Load(filter, decision, feedScope, activityDays));
            ActivityTitle = activityDays switch { <= 1 => "Activity, last 24 hours", 7 => "Activity, last 7 days", 30 => "Activity, last 30 days", _ => "Activity, last 3 months" };
            Apply(snapshot);
        }
        catch (Exception ex) when (ex is Microsoft.Data.Sqlite.SqliteException or IOException)
        {
            // The worker may be mid-checkpoint; keep the last view and try again on the next tick.
            StatusMessage = "Database busy, retrying…";
        }
        finally
        {
            Interlocked.Exchange(ref _refreshing, 0);
        }
    }

    private Snapshot Load(string filter, string decision, DataScope feedScope, int activityDays) => new(
        _services.Collector() == ServiceState.Running,
        _services.Worker() == ServiceState.Running,
        _db.Exists,
        _db.Summary(),
        _db.Recent(300, filter, decision, feedScope),
        _db.TopApps(DateTimeOffset.UtcNow.AddHours(-24)),
        activityDays <= 1 ? _db.Hourly(24) : _db.Daily(activityDays),
        _db.Models(),
        _db.Drc(),
        _learning.LastRun(),
        _services.IsRunning("learner"),
        ServiceControl.WorkerScoring(),
        _screen.Raw(),
        _screen.Memory(),
        _screen.Entries(1500),
        _db.Sources(), _screen.SessionNotes(), _screen.DayNotes(), _screen.Waiting());

    private sealed record Snapshot(
        bool Collector, bool Worker, bool DbExists, StorageSummary Summary, IReadOnlyList<RecordRow> Recent,
        IReadOnlyList<AppStat> Apps, IReadOnlyList<HourBucket> Hours, IReadOnlyList<ModelRow> Models, DrcComparison Drc,
        LearningResult LastLearning, bool LearnerRunning, string? WorkerScoring, RawBufferStats Raw, MemoryStats Memory,
        IReadOnlyList<MemoryRow> MemoryEntries, DataSources Sources,
        IReadOnlyDictionary<string, SessionNote> SessionNotes, IReadOnlyDictionary<string, DayNote> DayNotes, long Waiting);

    private void Apply(Snapshot s)
    {
        CollectorRunning = s.Collector;
        WorkerRunning = s.Worker;
        StatusMessage = s.DbExists ? "" : $"No database yet at {Settings.DbPath}. Start capture to create it.";

        var sum = s.Summary;
        RecordsText = sum.Records.ToString("N0");
        SaveRateText = sum.Records == 0 ? "–" : $"{sum.SaveRate:P0} saved";
        EmbeddingText = sum.Records == 0 ? "–" : $"{sum.WithEmbedding:N0} of {sum.Records:N0}";
        LastCaptureText = sum.LastCapture is { } last ? Ago(DateTimeOffset.Now - last) : "never";
        DiskText = FormatBytes(sum.BytesOnDisk);
        Hours = s.Hours;
        Sources = s.Sources;
        SessionNotes = s.SessionNotes;
        DayNotes = s.DayNotes;
        WaitingText = s.Waiting == 0 ? "No memories are waiting for a sentence."
            : $"{s.Waiting:N0} memor{(s.Waiting == 1 ? "y is" : "ies are")} waiting for a sentence from the local model.";

        var active = s.Models.FirstOrDefault(m => m.IsActive);
        ActiveModelText = active is null ? "Rule-based baseline" : $"{active.Algorithm} {active.Version}";
        Warning = BuildWarning(sum, active is null);
        DrcText = s.Drc.Available
            ? $"Keep rate: system {s.Drc.ModelKeepRate:P0}, DRC rules {s.Drc.DrcKeepRate:P0} ({s.Drc.BothKeep:N0} of {s.Drc.Recorded:N0} kept by both)"
            : "No DRC-rules verdicts yet (they are recorded once the worker runs with migration v3).";

        if (!LearningBusy) LearningText = s.LastLearning.Summary;
        LearnerText = s.LearnerRunning
            ? "Background learning is on: it runs when the PC is idle and plugged in."
            : Settings.BackgroundLearning ? "Background learning starts with capture." : "Background learning is off (Settings).";
        var expected = active is null ? "rules-1.0" : $"lightgbm-{active.Version}";
        RestartText = s.Worker && s.WorkerScoring is { } scoring && scoring != expected
            ? $"A new model is active ({(active is null ? "the rules" : active.Version)}) but the running worker still uses {scoring}. Restart capture to use it."
            : "";

        RawBufferText = s.Raw.Captures == 0
            ? "Nothing buffered"
            : $"{s.Raw.Captures:N0} captures buffered ({s.Raw.WithText:N0} with text, {FormatBytes(s.Raw.FileBytes)} on disk)"
              + (s.Raw.Oldest is { } oldest ? $", oldest {Ago(DateTimeOffset.Now - oldest)}" : "");
        MemoryStatsText = s.Memory.Entries == 0
            ? "No memory entries yet. They are built while the PC is idle and plugged in."
            : $"{s.Memory.Entries:N0} entries from {s.Memory.Documents:N0} pages ({FormatBytes(s.Memory.FileBytes)})";
        Replace(MemoryEntries, s.MemoryEntries);
        Replace(Recent, s.Recent);
        Replace(Models, s.Models);
        var max = Math.Max(1, s.Apps.Select(a => a.Records).DefaultIfEmpty(1).Max());
        Replace(TopApps, s.Apps.Select(a => new AppBar(a.App, a.Records, a.Saved, (double)a.Records / max)).ToList());
    }

    private string BuildWarning(StorageSummary sum, bool noModel)
    {
        if (sum.Records >= 20 && sum.EmbeddingCoverage < 0.5)
            return $"Only {sum.EmbeddingCoverage:P0} of records have an embedding. The worker was probably started without "
                   + "MobileCLIP model (mobileclip_s0_image.onnx in %LOCALAPPDATA%/context-importance, or the .pt checkpoint in Settings).";
        if (sum.Records >= 20 && noModel && sum.SaveRate > 0.7)
            return $"The rule-based baseline is saving {sum.SaveRate:P0} of contexts, so it is not selective yet. A trained model needs labelled data.";
        return "";
    }

    public async Task StartAsync()
    {
        try
        {
            StatusMessage = "Starting capture… (the worker loads MobileCLIP first)";
            await _services.StartAsync();
            StatusMessage = "";
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException)
        {
            StatusMessage = ex.Message;
        }
        await RefreshAsync();
    }

    public async Task StopAsync()
    {
        await Task.Run(_services.Stop);
        await RefreshAsync();
    }

    private static void Replace<T>(ObservableCollection<T> target, IReadOnlyList<T> items)
    {
        // Same length and contents is the common case between ticks: avoid redrawing the grid.
        if (target.Count == items.Count && target.SequenceEqual(items)) return;
        target.Clear();
        foreach (var item in items) target.Add(item);
    }

    internal static string Ago(TimeSpan span) =>
        span.TotalSeconds < 5 ? "just now"
        : span.TotalMinutes < 1 ? $"{(int)span.TotalSeconds} s ago"
        : span.TotalHours < 1 ? $"{(int)span.TotalMinutes} min ago"
        : span.TotalDays < 1 ? $"{(int)span.TotalHours} h ago"
        : $"{(int)span.TotalDays} d ago";

    internal static string FormatBytes(long bytes) =>
        bytes < 1024 * 1024 ? $"{bytes / 1024.0:F0} KB" : $"{bytes / 1024.0 / 1024.0:F1} MB";

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Notify(string name) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));

    private bool Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return false;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        return true;
    }
}

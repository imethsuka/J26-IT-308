namespace ContextDashboard.Data;

public sealed record StorageSummary(
    long Records,
    long Saved,
    long Discarded,
    long WithEmbedding,
    long Labelled,
    long ModelVersions,
    DateTimeOffset? FirstCapture,
    DateTimeOffset? LastCapture,
    long BytesOnDisk,
    int SchemaVersion,
    long RecentRecords = 0,
    long RecentSaved = 0)
{
    public static readonly StorageSummary Empty = new(0, 0, 0, 0, 0, 0, null, null, 0, 0);

    public double SaveRate => Records == 0 ? 0 : (double)Saved / Records;
    /// <summary>Save rate over the 24 hours up to the last capture: what the model serving now does. The all-time rate
    /// mixes the DRC history and every earlier model.</summary>
    public double RecentSaveRate => RecentRecords == 0 ? 0 : (double)RecentSaved / RecentRecords;
    public double EmbeddingCoverage => Records == 0 ? 0 : (double)WithEmbedding / Records;
}

public sealed record RecordRow(
    long Id,
    DateTimeOffset Time,
    string App,
    string Domain,
    string Title,
    string Decision,
    string Drc,
    string Source = "live");

public sealed record AppStat(string App, long Records, long Saved);

/// <summary>Records in one local-time hour, split by the stored decision.</summary>
public sealed record HourBucket(DateTime HourStart, long Saved, long Discarded)
{
    public long Total => Saved + Discarded;
}

public sealed record ModelRow(string Version, string Algorithm, bool IsActive, DateTimeOffset Created, string Metrics, string Notes);

/// <summary>How often the stored decision and DRC's verdict (models/drc_rules.py) agree.</summary>
public sealed record DrcComparison(long Recorded, long DrcWouldCapture, long ModelSaved, long BothKeep)
{
    public static readonly DrcComparison None = new(0, 0, 0, 0);
    public bool Available => Recorded > 0;
    public double DrcKeepRate => Recorded == 0 ? 0 : (double)DrcWouldCapture / Recorded;
    public double ModelKeepRate => Recorded == 0 ? 0 : (double)ModelSaved / Recorded;
}

// ---- Importance Engine page: everything here is computed from stored records, nothing is estimated ----

/// <summary>Records whose dwell time fell in one bucket, split by the stored decision.</summary>
public sealed record DwellBucket(string Label, long Saved, long Discarded)
{
    public long Total => Saved + Discarded;
}

/// <summary>One behavioural feature, averaged over recent records and scaled 0..1.</summary>
public sealed record SignalStat(string Name, double Value, string Detail);

/// <summary>A record still waiting for its delayed label (the adaptation job labels it after the horizon).</summary>
public sealed record PendingRow(DateTimeOffset Time, string App, string Title, string Decision, double DwellSeconds, int Revisits);

public sealed record EngineStats(
    long Records,
    long Pending,
    double? CommitRateRecent,
    double? CommitRatePrior,
    int SampleSize,
    IReadOnlyList<DwellBucket> Dwell,
    IReadOnlyList<SignalStat> Signals,
    IReadOnlyList<PendingRow> PendingNewest)
{
    public static readonly EngineStats Empty = new(0, 0, null, null, 0, [], [], []);

    /// <summary>Commit rate in the last 24 h minus the rate over the 7 days before it (fraction points).</summary>
    public double? Shift => CommitRateRecent is { } now && CommitRatePrior is { } before ? now - before : null;
}

/// <summary>Which records a view covers: everything, only live capture, or only the merged DRC history.</summary>
public enum DataScope { All, Live, History }

/// <summary>Live capture versus the DesktopResearchCollector history merged in by <c>merge_drc_into_live.py</c>.</summary>
public sealed record DataSources(
    DateTimeOffset? Cutoff,
    long HistoryCount, DateTimeOffset? HistoryFrom, DateTimeOffset? HistoryTo,
    long LiveCount, DateTimeOffset? LiveFrom, DateTimeOffset? LiveTo)
{
    public static readonly DataSources None = new(null, 0, null, null, 0, null, null);
    public bool HasHistory => HistoryCount > 0;
}

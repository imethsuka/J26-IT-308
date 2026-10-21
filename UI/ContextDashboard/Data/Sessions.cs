using System.Globalization;
using System.Security.Cryptography;
using System.Text;

namespace ContextDashboard.Data;

/// <summary>A title, tag and TLDR a language model wrote for one session (table <c>session_notes</c>).</summary>
public sealed record SessionNote(string Title, string Tag, string Tldr, string Model);

/// <summary>A recap a language model wrote for one day (table <c>day_notes</c>); <see cref="SessionsKey"/> says which sessions it covers.</summary>
public sealed record DayNote(string SessionsKey, string Tldr, string Model);

public sealed record AppTime(string App, double Seconds, int Memories);

public sealed record PageStat(string Title, string App, double Seconds, int Memories, double Importance);

/// <summary>One stretch of related work: important entries with no gap longer than <see cref="SessionBuilder.GapMinutes"/> and no longer than <see cref="SessionBuilder.MaxSpanMinutes"/> from first entry to last start.</summary>
public sealed class Session
{
    public required string Key { get; init; }
    public required IReadOnlyList<MemoryRow> Entries { get; init; }
    public SessionNote? Note { get; init; }

    public DateTimeOffset Start => Entries[0].Start;
    public DateTimeOffset End => Entries.Max(e => e.End ?? e.Start);
    public DateTime Day => Start.ToLocalTime().Date;
    public double ActiveSeconds => Entries.Sum(e => e.ActiveSeconds);
    public int WithText => Entries.Count(e => e.TextKept);
    public int ModelSummaries => Entries.Count(e => e.IsModelSummary);

    public IReadOnlyList<AppTime> Apps => SessionBuilder.TimeByApp(Entries);

    public IReadOnlyList<PageStat> Pages => Entries
        .GroupBy(e => (e.Title.Trim(), e.App))
        .Select(g => new PageStat(g.Key.Item1, g.Key.App, g.Sum(e => e.ActiveSeconds), g.Count(), g.Max(e => e.ImportanceValue)))
        .OrderByDescending(p => p.Importance * Math.Max(p.Seconds, 30))
        .ToList();

    /// <summary>The model's title when there is one, else the most important page's title, else the app.</summary>
    public string Title => !string.IsNullOrWhiteSpace(Note?.Title) ? Note!.Title : FallbackTitle;

    private string FallbackTitle
    {
        get
        {
            var best = Entries.OrderByDescending(e => e.ImportanceValue).First();
            return string.IsNullOrWhiteSpace(best.Title) ? $"{best.App} session" : best.Title.Trim();
        }
    }

    public string Tag => !string.IsNullOrWhiteSpace(Note?.Tag) ? Note!.Tag : (Apps.FirstOrDefault()?.App ?? "");

    public bool HasModelNote => !string.IsNullOrWhiteSpace(Note?.Tldr);

    /// <summary>The model's TLDR, or one line made only from the numbers.</summary>
    public string Summary
    {
        get
        {
            if (HasModelNote) return Note!.Tldr;
            var apps = Apps.Count;
            return $"{Entries.Count} memor{(Entries.Count == 1 ? "y" : "ies")} across {apps} app{(apps == 1 ? "" : "s")}, "
                   + $"{SessionBuilder.Minutes(ActiveSeconds)} active. Most important: {(string.IsNullOrWhiteSpace(Pages[0].Title) ? "a page" : Pages[0].Title)}.";
        }
    }
}

public sealed record DayView(
    DateTime Day,
    IReadOnlyList<Session> Sessions,
    IReadOnlyList<MemoryRow> AllEntries,
    string? ModelRecap,
    string ModelName)
{
    public int Memories => AllEntries.Count;
    public int Minor => AllEntries.Count - Sessions.Sum(s => s.Entries.Count);
    public int WithText => AllEntries.Count(e => e.TextKept);
    public int ModelSummaries => AllEntries.Count(e => e.IsModelSummary);
    public IReadOnlyList<AppTime> TimeByApp => SessionBuilder.TimeByApp(AllEntries);
    public double ActiveSeconds => AllEntries.Sum(e => e.ActiveSeconds);

    /// <summary>Recurring topics, ranked by how important and how often: pages (or sites) that came up most in the important entries.</summary>
    public IReadOnlyList<PageStat> TopOfMind => Sessions.SelectMany(s => s.Entries)
        .GroupBy(e => string.IsNullOrWhiteSpace(e.Title) ? e.Site : e.Title.Trim())
        .Where(g => !string.IsNullOrWhiteSpace(g.Key) && g.Key != "-")
        .Select(g => new PageStat(g.Key, g.First().App, g.Sum(e => e.ActiveSeconds), g.Count(), g.Sum(e => e.ImportanceValue)))
        .OrderByDescending(p => p.Importance)
        .Take(6).ToList();

    /// <summary>"Based on 38 memories, 12 with page text, 5 with a model summary" and what is left out.</summary>
    public string Coverage =>
        $"Based on {Memories} memor{(Memories == 1 ? "y" : "ies")}: {WithText} kept page text, {ModelSummaries} have a model summary. "
        + (Minor > 0 ? $"{Minor} minor ones count toward time only." : "");
}

/// <summary>
/// Groups memory entries into sessions and days. The rule and the key are the same as <c>adaptation/rollups.py</c>, so a note
/// the learning job wrote for a session is found here by its key; <c>SessionBuilderTests</c> and <c>test_rollups.py</c> share one example.
/// </summary>
public static class SessionBuilder
{
    public const int GapMinutes = 30;
    public const int MaxSpanMinutes = 60;     // a session is also cut this long after its first entry

    /// <summary>SHA-1 of the ids sorted ascending and joined by commas, first 16 hex characters (lower case).</summary>
    public static string Key(IEnumerable<long> ids) => Hash(string.Join(",", ids.OrderBy(i => i)));

    public static string KeyOfSessions(IEnumerable<string> sessionKeys) => Hash(string.Join(",", sessionKeys));

    private static string Hash(string text) =>
        Convert.ToHexString(SHA1.HashData(Encoding.ASCII.GetBytes(text))).ToLowerInvariant()[..16];

    private static bool Important(MemoryRow r) => r.Tier is "high" or "medium";

    public static IReadOnlyList<Session> Group(IEnumerable<MemoryRow> rows, IReadOnlyDictionary<string, SessionNote>? notes = null)
    {
        var important = rows.Where(Important).OrderBy(r => r.Start).ThenBy(r => r.Id).ToList();
        var sessions = new List<Session>();
        var current = new List<MemoryRow>();
        DateTimeOffset latestEnd = default;
        foreach (var r in important)
        {
            if (current.Count > 0 && (r.Start - latestEnd > TimeSpan.FromMinutes(GapMinutes)
                                      || r.Start - current[0].Start > TimeSpan.FromMinutes(MaxSpanMinutes)))
            {
                sessions.Add(Make(current, notes));
                current = [];
            }
            var end = r.End ?? r.Start;
            latestEnd = current.Count == 0 || end > latestEnd ? end : latestEnd;
            current.Add(r);
        }
        if (current.Count > 0) sessions.Add(Make(current, notes));
        return sessions;
    }

    private static Session Make(List<MemoryRow> entries, IReadOnlyDictionary<string, SessionNote>? notes)
    {
        var key = Key(entries.Select(e => e.Id));
        return new Session { Key = key, Entries = entries, Note = notes is not null && notes.TryGetValue(key, out var n) ? n : null };
    }

    /// <summary>Newest day first. Each day carries all its entries (for time) and its sessions (important ones).</summary>
    public static IReadOnlyList<DayView> Days(
        IReadOnlyList<MemoryRow> rows, IReadOnlyDictionary<string, SessionNote>? notes = null, IReadOnlyDictionary<string, DayNote>? dayNotes = null)
    {
        var sessions = Group(rows, notes);
        var byDay = rows.GroupBy(r => r.Start.ToLocalTime().Date).OrderByDescending(g => g.Key);
        var result = new List<DayView>();
        foreach (var g in byDay)
        {
            var daySessions = sessions.Where(s => s.Day == g.Key).OrderByDescending(s => s.Start).ToList();
            string? recap = null;
            var model = "";
            if (dayNotes is not null && dayNotes.TryGetValue(g.Key.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), out var dn)
                && daySessions.Count > 0
                && dn.SessionsKey == KeyOfSessions(daySessions.OrderBy(s => s.Start).Select(s => s.Key)) && !string.IsNullOrWhiteSpace(dn.Tldr))
            {
                recap = dn.Tldr;
                model = dn.Model;
            }
            result.Add(new DayView(g.Key, daySessions, g.OrderByDescending(r => r.Start).ToList(), recap, model));
        }
        return result;
    }

    public static IReadOnlyList<AppTime> TimeByApp(IEnumerable<MemoryRow> entries) => entries
        .GroupBy(e => string.IsNullOrWhiteSpace(e.App) ? "(unknown)" : e.App)
        .Select(g => new AppTime(g.Key, g.Sum(e => e.ActiveSeconds), g.Count()))
        .OrderByDescending(a => a.Seconds).ThenByDescending(a => a.Memories).ToList();

    public static string Minutes(double seconds) =>
        seconds < 60 ? "under a minute" : seconds < 3600 ? $"{Math.Round(seconds / 60):0} min" : $"{seconds / 3600:0.#} h";

}

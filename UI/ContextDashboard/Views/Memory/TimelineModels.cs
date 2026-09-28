using System.Globalization;
using ContextDashboard.Data;

namespace ContextDashboard.Views;

/// <summary>What the Memories page binds to. Everything shown is either a stored number or text a model wrote and is labelled as such.</summary>
public sealed record BarRow(string Label, string Value, double Share);

public sealed record LineRow(string When, string Text, string Meta);

public sealed class TimelineDayItem(DayView day)
{
    public DayView Day { get; } = day;

    public string Label => Day.Day == DateTime.Today ? "Today" : Day.Day == DateTime.Today.AddDays(-1) ? "Yesterday"
        : Day.Day.ToString("dddd d MMMM", CultureInfo.CurrentCulture);

    public string CountText => $"{Day.Sessions.Count} session{(Day.Sessions.Count == 1 ? "" : "s")}  ·  {Day.Memories} memories";
}

public sealed class TimelineSessionItem(Session session)
{
    public Session Session { get; } = session;
    public string Title => Session.Title;
    public string Tag => Session.Tag;
    public bool HasTag => Session.Tag.Length > 0;
    public string Summary => Session.Summary;
    public string TimeText => $"{Session.Start.ToLocalTime():HH:mm} to {Session.End.ToLocalTime():HH:mm}  ·  {SessionBuilder.Minutes(Session.ActiveSeconds)}";
    public string AppsText => string.Join("  ·  ", Session.Apps.Take(4).Select(a => a.App));
    public string Badge => Session.HasModelNote ? "model note" : "from the facts";
}

public sealed class SessionDetailVm
{
    public SessionDetailVm(Session s)
    {
        Title = s.Title;
        Subtitle = $"{s.Start.ToLocalTime():dddd d MMMM, HH:mm} to {s.End.ToLocalTime():HH:mm}  ·  {s.Entries.Count} memories  ·  {SessionBuilder.Minutes(s.ActiveSeconds)} active";
        SummaryLabel = s.HasModelNote ? $"TLDR  ·  written by {s.Note!.Model}" : "SUMMARY  ·  built from the stored facts, no model note yet";
        Summary = s.Summary;
        Coverage = $"{s.WithText} of {s.Entries.Count} kept page text, {s.ModelSummaries} have a model sentence.";

        var total = Math.Max(1, s.Apps.Sum(a => a.Seconds));
        Apps = s.Apps.Select(a => new BarRow(a.App, $"{SessionBuilder.Minutes(a.Seconds)}  ·  {a.Memories}", a.Seconds / total)).ToList();
        var maxImportance = Math.Max(0.01, s.Pages.Max(p => p.Importance));
        Pages = s.Pages.Take(6).Select(p => new BarRow(
            string.IsNullOrWhiteSpace(p.Title) ? $"({p.App})" : p.Title, $"{SessionBuilder.Minutes(p.Seconds)}  ·  {p.Memories}x", p.Importance / maxImportance)).ToList();
        Memories = s.Entries.Select(e => new LineRow($"{e.Start.ToLocalTime():HH:mm}", e.Headline,
            $"{e.Tier}{(e.IsModelSummary ? "  ·  model sentence" : "")}")).ToList();
    }

    public string Title { get; }
    public string Subtitle { get; }
    public string SummaryLabel { get; }
    public string Summary { get; }
    public string Coverage { get; }
    public IReadOnlyList<BarRow> Apps { get; }
    public IReadOnlyList<BarRow> Pages { get; }
    public IReadOnlyList<LineRow> Memories { get; }
}

public sealed class DayDetailVm
{
    public DayDetailVm(DayView d)
    {
        Title = "Recap  ·  " + (d.Day == DateTime.Today ? "today, " : "") + d.Day.ToString("dddd d MMMM", CultureInfo.CurrentCulture);
        HasModelRecap = d.ModelRecap is not null;
        RecapLabel = HasModelRecap ? $"DAY RECAP  ·  written by {d.ModelName}" : "DAY RECAP  ·  built from the stored facts, no model recap yet";
        Recap = d.ModelRecap ?? FactsRecap(d);
        Coverage = d.Coverage;

        var total = Math.Max(1, d.TimeByApp.Sum(a => a.Seconds));
        Apps = d.TimeByApp.Take(8).Select(a => new BarRow(a.App, $"{SessionBuilder.Minutes(a.Seconds)}  ·  {a.Memories}", a.Seconds / total)).ToList();
        var top = d.TopOfMind;
        var max = Math.Max(0.01, top.Select(t => t.Importance).DefaultIfEmpty(0).Max());
        TopOfMind = top.Select(t => new BarRow(t.Title, $"{t.Memories}x  ·  {SessionBuilder.Minutes(t.Seconds)}", t.Importance / max)).ToList();
        Sessions = d.Sessions.OrderBy(s => s.Start).Select(s => new LineRow(
            $"{s.Start.ToLocalTime():HH:mm}", s.Title, s.Summary)).ToList();
    }

    private static string FactsRecap(DayView d)
    {
        if (d.Sessions.Count == 0) return $"{d.Memories} minor memories, {SessionBuilder.Minutes(d.ActiveSeconds)} active. Nothing important enough to form a session.";
        var apps = d.TimeByApp.Take(3).Select(a => a.App);
        return $"{d.Sessions.Count} session{(d.Sessions.Count == 1 ? "" : "s")}, {SessionBuilder.Minutes(d.ActiveSeconds)} active, mostly in {string.Join(", ", apps)}.";
    }

    public string Title { get; }
    public bool HasModelRecap { get; }
    public string RecapLabel { get; }
    public string Recap { get; }
    public string Coverage { get; }
    public IReadOnlyList<BarRow> Apps { get; }
    public IReadOnlyList<BarRow> TopOfMind { get; }
    public IReadOnlyList<LineRow> Sessions { get; }
}

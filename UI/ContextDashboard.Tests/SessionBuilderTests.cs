using ContextDashboard.Data;
using ContextDashboard.Views;

namespace ContextDashboard.Tests;

/// <summary>
/// Sessions and days. The grouping rule and the session key must match adaptation/rollups.py: the learning job stores its
/// notes under that key, and tests/test_rollups.py asserts the same two keys for the same example.
/// </summary>
public class SessionBuilderTests
{
    private static readonly DateTimeOffset D = new(2026, 10, 20, 9, 0, 0, TimeSpan.Zero);

    private static MemoryRow Row(long id, DateTimeOffset start, double minutes = 5, string tier = "high", string app = "msedge",
                                 string title = "", string importance = "0.80", string summary = "", bool text = true) =>
        new(id, start, app, "docs.example.com", string.IsNullOrEmpty(title) ? $"Page {id}" : title, tier, importance, 1, "", text, summary,
            minutes * 60, 50, summary.Length > 0 ? "fake-1b" : "", start.AddMinutes(minutes));

    [Fact]
    public void Shared_example_groups_and_keys_match_the_python_side()
    {
        var rows = new[]
        {
            Row(1, D, 10),                         // 09:00-09:10
            Row(2, D.AddMinutes(35), 5),           // 09:35: 25 min after the first ended -> same session
            Row(3, D.AddMinutes(125), 5),          // 11:05: more than 30 min after 09:40 -> a new session
        };

        var sessions = SessionBuilder.Group(rows);

        Assert.Equal([[1L, 2L], [3L]], sessions.Select(s => s.Entries.Select(e => e.Id).ToArray()).ToArray());
        Assert.Equal(["2219fecb861ae82b", "77de68daecd823ba"], sessions.Select(s => s.Key).ToArray());
        Assert.Equal(SessionBuilder.Key([1, 2]), SessionBuilder.Key([2, 1]));
        Assert.Equal("1e3760a659ac9f05", SessionBuilder.KeyOfSessions(sessions.Select(s => s.Key)));
    }

    [Fact]
    public void A_long_entry_keeps_the_session_open_until_it_ends()
    {
        var sessions = SessionBuilder.Group([Row(1, D, 5), Row(2, D.AddMinutes(20), 70), Row(3, D.AddMinutes(55), 5)]);

        Assert.Single(sessions);
    }

    [Fact]
    public void A_long_busy_stretch_is_cut_into_hour_sessions()
    {
        var rows = Enumerable.Range(0, 8).Select(n => Row(n + 1, D.AddMinutes(20 * n), 5)).ToArray();

        var sessions = SessionBuilder.Group(rows);

        Assert.Equal([[1L, 2L, 3L, 4L], [5L, 6L, 7L, 8L]], sessions.Select(s => s.Entries.Select(e => e.Id).ToArray()).ToArray());
    }

    [Fact]
    public void Low_importance_entries_are_not_sessions_but_still_count_toward_the_day()
    {
        var rows = new[] { Row(1, D, tier: "low", app: "explorer"), Row(2, D.AddMinutes(10), tier: "medium", app: "code") };

        var day = SessionBuilder.Days(rows).Single();

        Assert.Single(day.Sessions);
        Assert.Equal(2, day.Memories);
        Assert.Equal(1, day.Minor);
        Assert.Equal(["code", "explorer"], day.TimeByApp.Select(a => a.App).Order().ToArray());
        Assert.Contains("1 minor", day.Coverage);
    }

    [Fact]
    public void A_session_uses_the_models_note_when_the_key_matches_and_the_facts_when_it_does_not()
    {
        var rows = new[] { Row(1, D, 10, title: "Payroll plan"), Row(2, D.AddMinutes(12), 5, title: "Docs") };
        var key = SessionBuilder.Key([1, 2]);
        var notes = new Dictionary<string, SessionNote> { [key] = new("Reviewing payroll", "finance", "You read the payroll plan.", "qwen") };

        var withNote = SessionBuilder.Group(rows, notes).Single();
        var without = SessionBuilder.Group(rows, new Dictionary<string, SessionNote> { ["other"] = new("x", "y", "z", "m") }).Single();

        Assert.True(withNote.HasModelNote);
        Assert.Equal(("Reviewing payroll", "finance", "You read the payroll plan."), (withNote.Title, withNote.Tag, withNote.Summary));
        Assert.False(without.HasModelNote);
        Assert.Equal("Payroll plan", without.Title);                         // the most important page
        Assert.Equal("msedge", without.Tag);
        Assert.StartsWith("2 memories across 1 app, ", without.Summary);     // numbers only, no invented content
    }

    [Fact]
    public void Time_per_app_and_key_pages_come_from_the_stored_numbers()
    {
        var rows = new[]
        {
            Row(1, D, 10, app: "msedge", title: "Plan", importance: "0.9"),
            Row(2, D.AddMinutes(11), 20, app: "code", title: "main.cs", importance: "0.5"),
            Row(3, D.AddMinutes(32), 5, app: "msedge", title: "Plan", importance: "0.7"),
        };

        var s = SessionBuilder.Group(rows).Single();

        Assert.Equal([("code", 1200.0, 1), ("msedge", 900.0, 2)], s.Apps.Select(a => (a.App, a.Seconds, a.Memories)).ToArray());
        Assert.Equal("Plan", s.Pages[0].Title);
        Assert.Equal(2, s.Pages[0].Memories);
        Assert.Equal(0.9, s.Pages[0].Importance, 3);
        Assert.Equal(2100.0, s.ActiveSeconds);
    }

    [Fact]
    public void A_day_recap_is_used_only_when_it_was_written_from_these_sessions()
    {
        var rows = new[] { Row(1, D, 10), Row(2, D.AddMinutes(125), 5) };
        var keys = SessionBuilder.Group(rows).Select(s => s.Key);
        var day = D.ToLocalTime().Date.ToString("yyyy-MM-dd");

        var fresh = SessionBuilder.Days(rows, null, new Dictionary<string, DayNote> { [day] = new(SessionBuilder.KeyOfSessions(keys), "You did two things.", "qwen") }).Single();
        var stale = SessionBuilder.Days(rows, null, new Dictionary<string, DayNote> { [day] = new("0000000000000000", "Old recap.", "qwen") }).Single();

        Assert.Equal("You did two things.", fresh.ModelRecap);
        Assert.Null(stale.ModelRecap);
    }

    [Fact]
    public void Top_of_mind_ranks_recurring_pages_by_importance()
    {
        var rows = new[]
        {
            Row(1, D, 5, title: "Budget", importance: "0.9"), Row(2, D.AddMinutes(6), 5, title: "Budget", importance: "0.8"),
            Row(3, D.AddMinutes(12), 5, title: "Lunch menu", importance: "0.6"),
        };

        var top = SessionBuilder.Days(rows).Single().TopOfMind;

        Assert.Equal(["Budget", "Lunch menu"], top.Select(t => t.Title).ToArray());
        Assert.Equal(2, top[0].Memories);
    }

    [Fact]
    public void Days_are_newest_first_and_a_day_with_no_important_entries_has_no_sessions()
    {
        var rows = new[] { Row(1, D, tier: "low"), Row(2, D.AddDays(1), tier: "high") };

        var days = SessionBuilder.Days(rows);

        Assert.Equal(2, days.Count);
        Assert.True(days[0].Day > days[1].Day);
        Assert.Empty(days[1].Sessions);
        Assert.Contains("Nothing important enough", new DayDetailVm(days[1]).Recap);
    }

    [Fact]
    public void The_detail_models_say_where_each_sentence_came_from()
    {
        var rows = new[] { Row(1, D, 10) };
        var note = new Dictionary<string, SessionNote> { [SessionBuilder.Key([1])] = new("Plan review", "plan", "You read the plan.", "qwen2.5") };

        var withNote = new SessionDetailVm(SessionBuilder.Group(rows, note).Single());
        var without = new SessionDetailVm(SessionBuilder.Group(rows).Single());

        Assert.Contains("written by qwen2.5", withNote.SummaryLabel);
        Assert.Contains("built from the stored facts", without.SummaryLabel);
        Assert.Equal("1 of 1 kept page text, 0 have a model sentence.", without.Coverage);
    }

    [Fact]
    public void Entries_without_an_end_time_do_not_break_grouping()
    {
        var open = Row(1, D) with { End = null };

        var s = SessionBuilder.Group([open, Row(2, D.AddMinutes(20))]).Single();

        Assert.Equal(2, s.Entries.Count);
    }
}

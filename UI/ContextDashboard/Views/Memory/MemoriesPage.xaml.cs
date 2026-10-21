using System.Collections.Specialized;
using System.Windows;
using System.Windows.Controls;
using ContextDashboard.Data;

namespace ContextDashboard.Views;

/// <summary>
/// The Memories page: a timeline of sessions on the left, and on the right either a day (recap, time per app, what is top of
/// mind) or one session (TLDR, time per app, key pages, the memories behind it). Numbers are computed from the stored entries;
/// the words a language model wrote are shown only where they exist, and labelled with the model's name.
/// </summary>
public partial class MemoriesPage : UserControl
{
    private MainViewModel? _vm;
    private string _signature = "";
    private string? _selectedKey;          // "day:yyyy-MM-dd" or "session:<key>", kept across refreshes
    private bool _rebuilding;

    public MemoriesPage()
    {
        InitializeComponent();
        Loaded += (_, _) =>
        {
            _vm = (MainViewModel)DataContext;
            _vm.MemoryEntries.CollectionChanged += OnEntriesChanged;
            _vm.PropertyChanged += (_, e) => { if (e.PropertyName == nameof(MainViewModel.Sources)) Rebuild(force: false); };
            Rebuild(force: true);
        };
        Unloaded += (_, _) =>
        {
            if (_vm is not null) _vm.MemoryEntries.CollectionChanged -= OnEntriesChanged;
        };
    }

    private void OnEntriesChanged(object? sender, NotifyCollectionChangedEventArgs e) => Dispatcher.BeginInvoke(() => Rebuild(force: false));

    private async void OnWriteSummaries(object sender, RoutedEventArgs e) => await ((MainViewModel)DataContext).WriteSummariesAsync();

    private void OnCancelWriter(object sender, RoutedEventArgs e) => ((MainViewModel)DataContext).CancelWriter();

    private void OnSearchChanged(object sender, RoutedEventArgs e) => Rebuild(force: true);

    private void Rebuild(bool force)
    {
        if (_vm is null) return;
        var rows = _vm.MemoryEntries.ToList();
        var query = SearchBox.Text?.Trim() ?? "";
        var signature = $"{rows.Count}|{rows.FirstOrDefault()?.Id}|{_vm.SessionNotes.Count}|{_vm.DayNotes.Count}|{query}";
        if (!force && signature == _signature) return;
        _signature = signature;

        var days = SessionBuilder.Days(rows, _vm.SessionNotes, _vm.DayNotes);
        var items = new List<object>();
        var matches = 0;
        foreach (var day in days)
        {
            var sessions = day.Sessions.Where(s => Matches(s, query)).ToList();
            if (query.Length > 0 && sessions.Count == 0) continue;
            items.Add(new TimelineDayItem(day));
            items.AddRange(sessions.Select(s => new TimelineSessionItem(s)));
            matches += sessions.Count;
        }

        _rebuilding = true;
        try
        {
            Timeline.ItemsSource = items;
            CountText.Text = rows.Count == 0 ? "" : query.Length > 0 ? $"{matches} matching session{(matches == 1 ? "" : "s")}"
                : $"{days.Sum(d => d.Sessions.Count)} sessions from {rows.Count:N0} memories";
            var keep = items.FindIndex(i => KeyOf(i) == _selectedKey);
            Timeline.SelectedIndex = keep >= 0 ? keep : items.Count > 0 ? 0 : -1;       // first load: today's day view
        }
        finally { _rebuilding = false; }
        Show(Timeline.SelectedItem);

        EmptyCard.Visibility = items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        if (rows.Count == 0)
        {
            EmptyTitle.Text = "No memories yet";
            EmptyText.Text = "Memories are built from screen text while the PC is idle and plugged in. Turn on Screen text, keep capturing, "
                             + "and run learning (Models) or let the background job do it.";
        }
        else
        {
            EmptyTitle.Text = "Nothing matches";
            EmptyText.Text = "Try a different search.";
        }
    }

    private static bool Matches(Session s, string query) =>
        query.Length == 0
        || s.Title.Contains(query, StringComparison.OrdinalIgnoreCase)
        || s.Summary.Contains(query, StringComparison.OrdinalIgnoreCase)
        || s.Tag.Contains(query, StringComparison.OrdinalIgnoreCase)
        || s.Entries.Any(e => e.App.Contains(query, StringComparison.OrdinalIgnoreCase)
                              || e.Site.Contains(query, StringComparison.OrdinalIgnoreCase)
                              || e.Title.Contains(query, StringComparison.OrdinalIgnoreCase)
                              || e.Summary.Contains(query, StringComparison.OrdinalIgnoreCase));

    private static string? KeyOf(object? item) => item switch
    {
        TimelineDayItem d => "day:" + d.Day.Day.ToString("yyyy-MM-dd"),
        TimelineSessionItem s => "session:" + s.Session.Key,
        _ => null,
    };

    private void OnTimelineSelected(object sender, SelectionChangedEventArgs e)
    {
        if (_rebuilding) return;
        _selectedKey = KeyOf(Timeline.SelectedItem);
        Show(Timeline.SelectedItem);
    }

    private void Show(object? item)
    {
        Detail.Content = item switch
        {
            TimelineDayItem d => new DayDetailVm(d.Day),
            TimelineSessionItem s => new SessionDetailVm(s.Session),
            _ => null,
        };
    }
}

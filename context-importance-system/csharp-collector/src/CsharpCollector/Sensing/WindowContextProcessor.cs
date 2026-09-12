using CsharpCollector.Events;
using CsharpCollector.Filtering;
using CsharpCollector.Output;

namespace CsharpCollector.Sensing;

/// <summary>
/// Turns raw window signals into emitted events. The order is the privacy guarantee:
/// process name -> app exclusion -> (browsers only) URL -> domain exclusion -> title -> emit.
/// An excluded context never has its title read, and an excluded app never has its URL read.
/// </summary>
internal sealed class WindowContextProcessor
{
    private readonly ExclusionFilter _filter;
    private readonly IWindowInfoSource _windows;
    private readonly IBrowserUrlReader _urls;
    private readonly HashSet<string> _browsers;
    private readonly IEventSink _sink;
    private readonly TimeProvider _time;

    private (string Process, string Title, string? Url)? _lastEmitted;
    private bool _inExcludedContext;

    public WindowContextProcessor(
        ExclusionFilter filter,
        IWindowInfoSource windows,
        IBrowserUrlReader urls,
        IEnumerable<string> browserProcesses,
        IEventSink sink,
        TimeProvider? time = null)
    {
        _filter = filter;
        _windows = windows;
        _urls = urls;
        _browsers = new HashSet<string>(browserProcesses.Select(ExclusionFilter.NormalizeAppName), StringComparer.OrdinalIgnoreCase);
        _sink = sink;
        _time = time ?? TimeProvider.System;
    }

    /// <summary>Returns true if the context is allowed, which means input may be counted.</summary>
    public bool Process(WindowSignal signal)
    {
        var process = _windows.GetProcessName(signal.Hwnd);
        if (_filter.IsAppExcluded(process))
            return EnterExcluded();

        string? url = null;
        if (_browsers.Contains(process!))
        {
            url = _urls.TryReadUrl(signal.Hwnd);
            if (_filter.IsUrlExcluded(url))
                return EnterExcluded();
        }

        var title = _windows.GetTitle(signal.Hwnd);
        var context = (process!, title, url);
        if (!_inExcludedContext && _lastEmitted == context)
            return true;

        _inExcludedContext = false;
        _lastEmitted = context;
        var type = signal.Kind == WindowSignalKind.Foreground ? "foreground" : "title_change";
        _sink.Emit(new WindowEvent(type, _time.GetUtcNow(), process!, title, url));
        return true;
    }

    private bool EnterExcluded()
    {
        if (!_inExcludedContext)
        {
            _inExcludedContext = true;
            _sink.Emit(new ExcludedContextEvent(_time.GetUtcNow()));
        }
        return false;
    }
}

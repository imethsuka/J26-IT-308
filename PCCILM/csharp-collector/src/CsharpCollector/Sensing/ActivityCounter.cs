namespace CsharpCollector.Sensing;

internal readonly record struct ActivityCounts(int KeyPresses, int MouseClicks, int MouseScrolls, int MouseMoves);

/// <summary>
/// Rolling-window input counts made of 1-second buckets. The Record* methods run on the hook
/// thread; Rotate/Sum run on the ticker thread.
/// </summary>
internal sealed class ActivityCounter
{
    private int _keys, _clicks, _scrolls, _moves;

    private readonly ActivityCounts[] _buckets;
    private int _next;
    private int _filled;

    public ActivityCounter(int windowSeconds)
    {
        _buckets = new ActivityCounts[windowSeconds];
    }

    // Held keys auto-repeat, so a held key counts more than once. The LL hook exposes no repeat
    // flag, and telling repeats apart would mean tracking key codes, which we never read.
    public void RecordKeyPress() => Interlocked.Increment(ref _keys);
    public void RecordClick() => Interlocked.Increment(ref _clicks);
    public void RecordScroll() => Interlocked.Increment(ref _scrolls);
    public void RecordMove() => Interlocked.Increment(ref _moves);

    /// <summary>Closes the current 1-second bucket. Call once per second.</summary>
    public void Rotate()
    {
        _buckets[_next] = new ActivityCounts(
            Interlocked.Exchange(ref _keys, 0),
            Interlocked.Exchange(ref _clicks, 0),
            Interlocked.Exchange(ref _scrolls, 0),
            Interlocked.Exchange(ref _moves, 0));
        _next = (_next + 1) % _buckets.Length;
        if (_filled < _buckets.Length)
            _filled++;
    }

    /// <summary>Counts over the closed buckets, plus how many seconds they actually cover.</summary>
    public (ActivityCounts Counts, int Seconds) Sum()
    {
        int keys = 0, clicks = 0, scrolls = 0, moves = 0;
        foreach (var b in _buckets)
        {
            keys += b.KeyPresses;
            clicks += b.MouseClicks;
            scrolls += b.MouseScrolls;
            moves += b.MouseMoves;
        }
        return (new ActivityCounts(keys, clicks, scrolls, moves), _filled);
    }
}

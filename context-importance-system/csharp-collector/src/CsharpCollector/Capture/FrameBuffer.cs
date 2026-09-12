namespace CsharpCollector.Capture;

/// <summary>
/// In-memory buffer of the most recent frames. It holds only the latest frame by default
/// (capacity 1); when full, adding a frame evicts the oldest. Nothing is ever written to disk.
/// It will be read by the IPC layer in a later phase.
/// </summary>
internal sealed class FrameBuffer
{
    private readonly Queue<Frame> _frames;
    private readonly object _lock = new();

    public FrameBuffer(int capacity = 1)
    {
        if (capacity < 1)
            throw new ArgumentOutOfRangeException(nameof(capacity), "Capacity must be at least 1.");
        Capacity = capacity;
        _frames = new Queue<Frame>(capacity);
    }

    public int Capacity { get; }

    public int Count
    {
        get { lock (_lock) return _frames.Count; }
    }

    public void Add(Frame frame)
    {
        lock (_lock)
        {
            _frames.Enqueue(frame);
            while (_frames.Count > Capacity)
                _frames.Dequeue();
        }
    }

    public Frame? PeekLatest()
    {
        lock (_lock)
            return _frames.Count == 0 ? null : _frames.Last();
    }

    /// <summary>Oldest first.</summary>
    public IReadOnlyList<Frame> Snapshot()
    {
        lock (_lock)
            return _frames.ToArray();
    }

    public void Clear()
    {
        lock (_lock)
            _frames.Clear();
    }
}

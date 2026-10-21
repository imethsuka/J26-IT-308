namespace CsharpCollector.Capture;

/// <summary><paramref name="Difference"/> is null when there is no baseline yet.</summary>
internal readonly record struct ChangeDecision(bool Changed, double? Difference);

/// <summary>
/// Decides whether a thumbnail differs enough to justify a full capture. It compares against
/// the last accepted thumbnail, not the previous probe, so a slow drift still triggers once it
/// adds up to the threshold.
/// </summary>
internal sealed class ScreenChangeDetector
{
    private byte[]? _baseline;

    /// <param name="threshold">Mean absolute grey difference, 0-255. A difference equal to the threshold counts as changed, so 0 captures on every probe.</param>
    public ScreenChangeDetector(double threshold)
    {
        if (threshold is < 0 or > 255 || double.IsNaN(threshold))
            throw new ArgumentOutOfRangeException(nameof(threshold), "Threshold must be within 0-255.");
        Threshold = threshold;
    }

    public double Threshold { get; }

    public ChangeDecision Evaluate(byte[] thumbnail)
    {
        if (_baseline is null)
            return new ChangeDecision(true, null);

        var difference = Thumbnail.MeanAbsoluteDifference(thumbnail, _baseline);
        return new ChangeDecision(difference >= Threshold, difference);
    }

    /// <summary>Call only once the triggered frame has actually been stored.</summary>
    public void Accept(byte[] thumbnail) => _baseline = thumbnail;
}

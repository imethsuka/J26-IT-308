using CAPRLMComparison.Models;
using ScottPlot;

namespace CAPRLMComparison.Services;

/// <summary>One bar chart per metric, CAPRLM beside each baseline. Takes a plain ScottPlot Plot so it
/// doesn't depend on WPF (and can be rendered to an image).</summary>
public static class MetricCharts
{
    public static readonly string[] FilterOrder =
        ["caprlm", "rule_based", "static_masking", "anonymization", "pseudonymization"];

    private static readonly Dictionary<string, string> ShortNames = new()
    {
        ["caprlm"] = "CAPRLM",
        ["rule_based"] = "Rule-based",
        ["static_masking"] = "Static mask",
        ["anonymization"] = "Anonymize",
        ["pseudonymization"] = "Pseudonym",
    };

    public sealed record MetricSpec(string Title, bool LowerIsBetter, Func<MetricSet, double> Value);

    public static readonly MetricSpec[] Specs =
    [
        new("Precision", false, m => m.Precision),
        new("Recall", false, m => m.Recall),
        new("F1", false, m => m.F1),
        new("Leakage rate", true, m => m.LeakageRate),
        new("Re-identification rate", true, m => m.ReidentificationRate),
        new("Semantic similarity (utility)", false, m => m.SemanticSimilarity),
    ];

    private static readonly Color Highlight = Color.FromHex("#2563EB");
    private static readonly Color Baseline = Color.FromHex("#94A3B8");

    public static void Build(Plot plot, MetricSpec spec, EvalResults results, bool warn)
    {
        plot.Clear();
        var names = FilterOrder.Where(results.Metrics.ContainsKey).ToArray();

        var bars = names.Select((n, i) =>
        {
            var v = spec.Value(results.Metrics[n]);
            return new Bar
            {
                Position = i,
                Value = v,
                Size = 0.7,
                FillColor = n == "caprlm" ? Highlight : Baseline,
                Label = v.ToString("0.00"),
            };
        }).ToArray();

        var barPlot = plot.Add.Bars(bars);
        barPlot.ValueLabelStyle.FontSize = 12;

        plot.Axes.Bottom.TickGenerator = new ScottPlot.TickGenerators.NumericManual(
            names.Select((n, i) => new Tick(i, ShortNames.GetValueOrDefault(n, n))).ToArray());
        plot.Axes.Bottom.TickLabelStyle.FontSize = 11;
        plot.Axes.SetLimitsY(0, 1.15);
        plot.Axes.SetLimitsX(-0.6, names.Length - 0.4);

        var arrow = spec.LowerIsBetter ? "  (lower is better)" : "";
        plot.Title(spec.Title + arrow + (warn ? "  [not reportable data]" : ""));
        plot.Axes.Title.Label.FontSize = 13;
    }
}

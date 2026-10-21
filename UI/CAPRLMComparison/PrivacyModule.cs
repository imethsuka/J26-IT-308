using CAPRLMComparison.Services;
using CAPRLMComparison.Views;
using Suite.Core;
using Wpf.Ui.Controls;

namespace CAPRLMComparison;

/// <summary>Member 2's slice of the app: privacy review, policy, and the comparison against the baselines.</summary>
public sealed class PrivacyModule : IModule, IDisposable
{
    private readonly ApiClient _api = new();
    private readonly CaprlmBackend _backend = new();

    public PrivacyModule()
    {
        Pages =
        [
            new ModulePage("review", "Privacy review", SymbolRegular.ShieldCheckmark24,
                () => { var p = new ReviewPage(); p.Init(_api); return p; }),
            new ModulePage("policy", "Policy & risk", SymbolRegular.Options24,
                () => { var p = new PolicyPage(); p.Init(_api); return p; })
            { OnShown = page => ((PolicyPage)page).LoadAsync() },
            new ModulePage("compare", "Live comparison", SymbolRegular.ArrowSwap24,
                () => { var p = new ComparePage(); p.Init(_api); return p; }),
            new ModulePage("metrics", "Metrics", SymbolRegular.DataBarVertical24,
                () => { var p = new MetricsPage(); p.Init(_api); return p; })
            { OnShown = page => ((MetricsPage)page).HasResults ? Task.CompletedTask : ((MetricsPage)page).LoadAsync() },
        ];
        Backends = [_backend];
    }

    public string Section => "PRIVACY";
    public IReadOnlyList<ModulePage> Pages { get; }
    public IReadOnlyList<IBackend> Backends { get; }

    public void Dispose() => _api.Dispose();
}

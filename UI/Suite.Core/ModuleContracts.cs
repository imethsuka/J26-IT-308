using System.Windows;
using Wpf.Ui.Controls;

namespace Suite.Core;

/// <summary>
/// One member's slice of the app. The shell knows only this contract: it lists each module's pages in
/// the sidebar and keeps its backends running. Modules never reference each other.
/// </summary>
public interface IModule
{
    /// <summary>Sidebar section heading, e.g. "MEMORY".</summary>
    string Section { get; }
    IReadOnlyList<ModulePage> Pages { get; }
    IReadOnlyList<IBackend> Backends { get; }
}

/// <summary>A sidebar entry. <see cref="Create"/> runs once, the first time the page is opened.</summary>
public sealed class ModulePage(string id, string title, SymbolRegular icon, Func<FrameworkElement> create)
{
    public string Id { get; } = id;
    public string Title { get; } = title;
    public SymbolRegular Icon { get; } = icon;
    public Func<FrameworkElement> Create { get; } = create;

    /// <summary>Runs every time the page is shown (after the first creation), e.g. to reload stats.</summary>
    public Func<FrameworkElement, Task>? OnShown { get; init; }
}

/// <summary>A background process a module depends on (Python worker, privacy service, ...).</summary>
public interface IBackend
{
    string Name { get; }

    /// <summary>True when the app should start this backend on its own at launch.</summary>
    bool StartWithApp { get; }

    Task<bool> IsRunningAsync();

    /// <summary>Starts it if it is not up. Returns null when it is running, otherwise what went wrong.</summary>
    Task<string?> EnsureStartedAsync();

    /// <summary>Called when the app exits. A backend stops only what this app started itself.</summary>
    Task ShutdownAsync();
}

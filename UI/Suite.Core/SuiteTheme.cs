using System.Windows;
using System.Windows.Media;
using Wpf.Ui.Appearance;
using Wpf.Ui.Markup;

namespace Suite.Core;

public static class SuiteTheme
{
    /// <summary>
    /// Installs the Fluent resources, the shared palette and the green accent on the running Application.
    /// Call once, right after the Application exists and before any window is created.
    /// </summary>
    public static void Apply(Application app)
    {
        var merged = app.Resources.MergedDictionaries;
        merged.Add(new ThemesDictionary { Theme = ApplicationTheme.Light });
        merged.Add(new ControlsDictionary());
        merged.Add(new ResourceDictionary
        {
            Source = new Uri("pack://application:,,,/Suite.Core;component/Themes/Palette.xaml", UriKind.Absolute),
        });
        ApplicationAccentColorManager.Apply(Color.FromRgb(0x3F, 0x7D, 0x5E), ApplicationTheme.Light);
    }
}

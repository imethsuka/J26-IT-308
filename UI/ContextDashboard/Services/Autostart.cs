using Microsoft.Win32;

namespace ContextDashboard.Services;

/// <summary>
/// Start with Windows: one per-user Run entry for the tray. The tray then starts the collector and
/// worker (<c>--autostart</c>). It has to be a logon entry in the user's session, not a Windows
/// service: the collector's hooks and screen capture need the interactive desktop.
/// </summary>
public static class Autostart
{
    private const string KeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "ContextImportanceTray";

    public static bool IsEnabled()
    {
        using var key = Registry.CurrentUser.OpenSubKey(KeyPath);
        return key?.GetValue(ValueName) is string;
    }

    public static void Set(bool enabled)
    {
        using var key = Registry.CurrentUser.CreateSubKey(KeyPath);
        if (enabled)
        {
            var exe = Environment.ProcessPath ?? throw new InvalidOperationException("no process path");
            key.SetValue(ValueName, $"\"{exe}\" --tray --autostart");
        }
        else
        {
            key.DeleteValue(ValueName, throwOnMissingValue: false);
        }
    }
}

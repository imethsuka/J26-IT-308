; Inno Setup script (https://jrsoftware.org/isinfo.php). Build with:  iscc packaging\ContextImportance.iss
; Run packaging\build_bundle.ps1 first: it creates stage\ContextImportance\ that this packs.
; Per-user install: no administrator rights, nothing outside the user's profile.

[Setup]
AppId={{6C1D2B1E-4F0A-4B7E-9C55-2D8E7A3F9A10}
AppName=Context Importance
AppVersion=0.1.0
AppPublisher=J26-IT-308 Member 1
DefaultDirName={localappdata}\Programs\ContextImportance
DefaultGroupName=Context Importance
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=ContextImportance-Setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayName=Context Importance
DisableProgramGroupPage=yes

[Files]
Source: "stage\ContextImportance\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{userprograms}\Context Importance"; Filename: "{app}\ContextDashboard.exe"

[Tasks]
Name: autostart; Description: "Start capture automatically when I sign in to Windows"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ContextImportanceTray"; \
    ValueData: """{app}\ContextDashboard.exe"" --tray --autostart"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\ContextDashboard.exe"; Description: "Open Context Importance"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; The background processes hold files open; stop them before the files are removed.
Filename: "{sys}\taskkill.exe"; Parameters: "/IM CsharpCollector.exe /F"; Flags: runhidden; RunOnceId: "StopCollector"
Filename: "{sys}\taskkill.exe"; Parameters: "/IM worker.exe /F"; Flags: runhidden; RunOnceId: "StopWorker"
Filename: "{sys}\taskkill.exe"; Parameters: "/IM caprlm-service.exe /F"; Flags: runhidden; RunOnceId: "StopCaprlm"
Filename: "{sys}\taskkill.exe"; Parameters: "/IM ContextDashboard.exe /F"; Flags: runhidden; RunOnceId: "StopUi"

[Code]
// Captured data (context.db, learned models, logs) is the user's. Removing the app keeps it unless they say otherwise.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\context-importance');
    if DirExists(DataDir) then
      if MsgBox('Also delete your captured data, learned models and logs?' + #13#10 + DataDir + #13#10#13#10 +
                'Choose No to keep them (for example to reinstall later).', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;

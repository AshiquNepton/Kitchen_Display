; Inno Setup script - open in Inno Setup 6 and press Compile (or run build.bat). Run build.bat/pyinstaller first.
#define AppName "Nepton KDS"
#define AppVersion "1.0.0"

[Setup]
AppId={{7B1C6E52-4C3A-4E0B-9B8D-2A5F0D9E6A11}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\NeptonKDS
DefaultGroupName={#AppName}
UsePreviousAppDir=no
UsePreviousGroup=no
OutputDir=installer_output
OutputBaseFilename=KDS_Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
SetupIconFile=kds\static\kds\icon.ico

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start the KDS automatically when Windows starts"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "firewall"; Description: "Allow other screens on the network to open the KDS (opens TCP port 8000)"; GroupDescription: "Network:"

[InstallDelete]
; remove the loose files left by the earlier multi-file build
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\KDS.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "kds\static\kds\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; kds_config.json (saved connection) is written here, so every Windows user must be able to modify it
Name: "{commonappdata}\NeptonKDS"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\KDS.exe"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\KDS.exe"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon
Name: "{commonstartup}\{#AppName}"; Filename: "{app}\KDS.exe"; IconFilename: "{app}\icon.ico"; Tasks: startup

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Nepton KDS"" dir=in action=allow protocol=TCP localport=8000"; Flags: runhidden; Tasks: firewall
Filename: "{app}\KDS.exe"; Description: "Start the KDS now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Nepton KDS"""; Flags: runhidden; RunOnceId: "DelFirewall"

[Code]
{ KDS.exe runs without a window, so stop it before installing / uninstalling (otherwise its files are locked) }
procedure KillKds;
var R: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM KDS.exe', '', SW_HIDE, ewWaitUntilTerminated, R);
end;

procedure InitializeWizard;
var
  CurDir: String;
  CurDrive: String;
begin
  CurDir := WizardDirValue();
  CurDrive := ExtractFileDrive(CurDir);
  if (Length(CurDrive) > 0) and (not DirExists(CurDrive + '\')) then
  begin
    WizardForm.DirEdit.Text := ExpandConstant('{autopf}\NeptonKDS');
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillKds;
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then KillKds;
end;

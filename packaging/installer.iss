; Inno Setup script (https://jrsoftware.org/isinfo.php): wraps dist\personal-agent into PersonalAgentSetup.exe.
; Compile with:  iscc packaging\installer.iss        (after scripts\build.ps1)
; NOT compiled or tested in this repository's CI: treat it as a starting point and see docs/development.md (Packaging).
#define AppName "personal-agent"
#define AppVersion "0.1.0"

[Setup]
AppId={{B1C3F5B0-7A52-4C63-9B6E-3E0E6F1A9A10}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PersonalAgentSetup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}

[Files]
Source: "..\dist\personal-agent\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\ui\agent-ui.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ui\agent-ui.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start {#AppName} when I sign in to Windows"; Flags: unchecked

[Registry]
; the UI also has its own Settings -> General -> "Start with Windows" switch (same HKCU Run key)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "personal-agent-ui"; \
  ValueData: """{app}\ui\agent-ui.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\ui\agent-ui.exe"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; stop a running daemon so its files can be removed (data under %APPDATA%\personal-agent is kept on purpose)
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM agent-core.exe"; Flags: runhidden; RunOnceId: "StopDaemon"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM agent-ui.exe"; Flags: runhidden; RunOnceId: "StopUi"

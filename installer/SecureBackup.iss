; Inno Setup script: turns the PyInstaller output (dist\SecureBackup) into one
; SecureBackup-Setup.exe that installs the desktop client for the current user.
;
; Build order:   python build_client.py      (creates dist\SecureBackup)
;                then compile this file:  ISCC installer\SecureBackup.iss
; Result:        dist\installer\SecureBackup-Setup.exe
;
; Only the client is installed. The server stays in the project folder and is
; started separately (python -m config.securerun), so it is not part of this file.

#define AppName "Secure Multi-Cloud Backup"
#define AppExe "SecureBackup.exe"
#define AppVersion "1.0.0"

[Setup]
; Identifies this program to Windows, so a newer setup upgrades the installed one
; instead of installing a second copy. Never change it once released.
AppId={{062BC3E3-C4A8-4405-99C2-EA3437EA242A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Graduation Project
; "lowest" = no administrator prompt. With it, {autopf} means the user's own
; %LocalAppData%\Programs instead of C:\Program Files.
PrivilegesRequired=lowest
DefaultDirName={autopf}\SecureBackup
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=SecureBackup-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Windows' "Apps" list shows this icon and an Uninstall button.
UninstallDisplayIcon={app}\{#AppExe}

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
; Everything PyInstaller produced: the .exe, its _internal support folder, and
; dev-ca.pem (the certificate the client trusts for the local development server).
Source: "..\dist\SecureBackup\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

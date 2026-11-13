; Per-user ProxyManager installer. No admin rights, no autostart.
; Version is injected by package.bat / CI: ISCC /DMyAppVersion=1.4.0
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "ProxyManager"
#define MyAppPublisher "MasqueradeQ"
#define MyAppURL "https://github.com/quji762/ProxyManager"

[Setup]
AppId={{A7C3E1B4-6D28-4F0A-9B55-2E8C7D41A6F0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=ProxyManager-setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\ProxyManager.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=LICENSE
CloseApplications=yes
RestartApplications=no

; The official compiler install does not ship ChineseSimplified.isl.
; The app UI is bilingual; the short setup wizard stays on the built-in English.
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\ProxyManager.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\ProxyManager.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\ProxyManager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ProxyManager.exe"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

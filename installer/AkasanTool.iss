#define AppName "赤ずきんと不思議な洞窟-tool"
#define AppExe AppName + ".exe"
#define AppRepository "https://github.com/wishwide2/cheat-tool"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppBinary
  #define AppBinary "..\dist\AkasanTool.exe"
#endif

[Setup]
AppId={{1CDA01B8-57A3-4D8A-9B08-25B37B0F7BD4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Akasan Workshop contributors
AppPublisherURL={#AppRepository}
AppSupportURL={#AppRepository}/issues
AppUpdatesURL={#AppRepository}/releases
DefaultDirName={localappdata}\Programs\AkasanTool
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
MinVersion=10.0.10240
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
OutputDir=..\dist
OutputBaseFilename=AkasanTool-v{#AppVersion}-Windows-x64-Setup
SetupIconFile=..\ui_icon.ico
UninstallDisplayIcon={app}\{#AppExe}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
WizardStyle=modern dynamic
Compression=lzma2
SolidCompression=yes
RestartApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppBinary}"; DestDir: "{app}"; DestName: "{#AppExe}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "licenses\*.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Check: not WizardNoIcons
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

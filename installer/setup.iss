; 转转 — Windows 安装包脚本（Inno Setup 6）
;
; 用法：
;   1) 安装 Inno Setup 6：https://jrsoftware.org/isdl.php
;   2) 先在仓库根目录跑  python build.py  ，生成 dist\转转.exe
;   3) 再在仓库根目录执行  iscc installer\setup.iss
;
; 所有路径都写成「相对于本 .iss 文件」的形式，换台机器不用改。

#define MyAppName "转转"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Naiqey.千鵺"
#define MyAppExeName "转转.exe"
; installer\ 的上一级即仓库根目录
#define RepoRoot AddBackslash(SourcePath) + ".."

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; 产物输出到仓库根目录的 dist\
OutputDir={#RepoRoot}\dist
OutputBaseFilename=转转_Setup_{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; 装到用户目录，不弹 UAC
PrivilegesRequired=lowest
SetupIconFile=compiler:SetupClassicIcon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#RepoRoot}\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

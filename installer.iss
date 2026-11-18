; 全童全星上课点名系统 —— Inno Setup 安装脚本
; 生成标准的 setup.exe：安装向导 + 开始菜单/桌面快捷方式 + 卸载器。
; 数据库用内置 SQLite，数据自动存到 %APPDATA%\全童全星上课点名系统，
; 所以即使装到 Program Files（默认只读）也能正常读写数据。

#define MyAppName "全童全星上课点名系统"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "全童全星"
#define MyAppExeName "dmxt.exe"

[Setup]
AppId={{8F2A3B1C-5D4E-4A6B-9C7D-1E2F3A4B5C6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; 默认不让用户改安装目录也可以，但保留选择更友好
DisableProgramGroupPage=yes
; 输出文件名与目录
OutputDir=installer
OutputBaseFilename=全童全星上课点名系统_Setup
; 安装向导外观
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 关闭时会提示「程序可能还在运行」之类的行为控制
CloseApplications=yes
RestartApplications=no
; 权限：需要管理员写 Program Files
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
; 语言
; 中文简体需要额外的语言文件，这里先不引，保持英文向导（简洁可靠）

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："
Name: "desktopicon\common"; Description: "对所有用户"; GroupDescription: "附加图标："; Flags: exclusive
Name: "desktopicon\user"; Description: "仅当前用户"; GroupDescription: "附加图标："; Flags: exclusive unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\app.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "assets\logo.png"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\app.ico"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清理程序目录（数据在 %APPDATA%，不在这里，保留用户数据）
Type: filesandordirs; Name: "{app}\assets"

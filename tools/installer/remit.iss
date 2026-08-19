; Remit 安装脚本 —— 由 tools/package_win.ps1 调用 ISCC 编译
; 用法: ISCC.exe remit.iss /DStageDir=<staging根目录> /DOutDir=<输出目录>

#define MyAppName "Remit 数学建模工作台"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Remit"

[Setup]
AppId={{6E2F9ACA-4B3A-4F7E-9D1C-2B5A8E61D0F3}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Remit
DefaultGroupName=Remit
UninstallDisplayIcon={app}\assets\remit-m-icon.ico
OutputDir={#OutDir}
OutputBaseFilename=RemitSetup
Compression=lzma2/ultra
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile={#StageDir}\Remit\assets\remit-m-icon.ico
DisableProgramGroupPage=yes
RestartIfNeededByRun=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "{#StageDir}\Remit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Remit 数学建模工作台"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-B ""{app}\tools\remit_prod_app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\remit-m-icon.ico"
Name: "{group}\停止 Remit 服务"; Filename: "{app}\runtime\python\python.exe"; Parameters: "-B ""{app}\tools\remit_prod_app.py"" --stop"; WorkingDir: "{app}"; IconFilename: "{app}\assets\remit-m-icon.ico"
Name: "{autodesktop}\Remit 数学建模工作台"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-B ""{app}\tools\remit_prod_app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\remit-m-icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\python\python.exe"; Parameters: "-B ""{app}\tools\remit_prod_app.py"" --check"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "正在检查安装完整性..."

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\backend\logs"
Type: filesandordirs; Name: "{app}\runtime\python\Lib\*\__pycache__"
Type: filesandordirs; Name: "{app}\runtime\python\Lib\site-packages\*\__pycache__"

[UninstallRun]
Filename: "{app}\runtime\python\python.exe"; Parameters: "-B ""{app}\tools\remit_prod_app.py"" --stop"; WorkingDir: "{app}"; Flags: runhidden; RunOnceId: "StopRemitServices"

[Messages]
WelcomeLabel2=Remit 是本地优先的数学建模工作台。[n][n]安装包已内置 Python 运行时与 Redis，无需安装 MATLAB 即可使用全部功能；如果电脑装有 MATLAB，程序会自动优先调用它。[n][n]首次打开后，请在工作台右上角的 API 配置中填写你的模型密钥。

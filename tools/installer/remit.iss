; Remit 安装脚本 —— 由 tools/package_win.ps1 调用 ISCC 编译
; 用法: ISCC.exe remit.iss /DStageDir=<staging根目录> /DOutDir=<输出目录>

#define MyAppName "Remit 数学建模工作台"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Remit contributors"

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
; max 降低大体积 Python 运行时的编译内存占用，避免与其他构建争用内存。
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile={#StageDir}\Remit\assets\remit-m-icon.ico
LicenseFile={#StageDir}\Remit\LICENSE
DisableProgramGroupPage=yes
RestartIfNeededByRun=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "{#StageDir}\Remit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Remit 数学建模工作台"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-B ""{app}\tools\remit_prod_app.pyc"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\remit-m-icon.ico"
Name: "{group}\停止 Remit 服务"; Filename: "{app}\runtime\python\python.exe"; Parameters: "-B ""{app}\tools\remit_prod_app.pyc"" --stop"; WorkingDir: "{app}"; IconFilename: "{app}\assets\remit-m-icon.ico"
Name: "{autodesktop}\Remit 数学建模工作台"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-B ""{app}\tools\remit_prod_app.pyc"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\remit-m-icon.ico"; Tasks: desktopicon

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if not Exec(ExpandConstant('{app}\runtime\python\python.exe'),
      '-B "' + ExpandConstant('{app}\tools\remit_prod_app.pyc') + '" --check',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      RaiseException('无法运行 Remit 安装完整性检查。');
    if ResultCode <> 0 then
      RaiseException('Remit 安装完整性检查失败，请重新安装并查看 logs 文件夹。');
  end;
end;

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\backend\logs"
Type: filesandordirs; Name: "{app}\runtime\python\Lib\*\__pycache__"
Type: filesandordirs; Name: "{app}\runtime\python\Lib\site-packages\*\__pycache__"

[UninstallRun]
Filename: "{app}\runtime\python\python.exe"; Parameters: "-B ""{app}\tools\remit_prod_app.pyc"" --stop"; WorkingDir: "{app}"; Flags: runhidden; RunOnceId: "StopRemitServices"

[Messages]
WelcomeLabel2=Remit 是本地优先的数学建模工作台。[n][n]安装包已内置 Python 运行时与 Redis，无需安装 MATLAB 即可使用全部功能；如果电脑装有 MATLAB，程序会自动优先调用它。[n][n]首次打开后，请在工作台右上角的 API 配置中填写你的模型密钥。[n][n]当前源码已完成独立实现整改；分发前仍请阅读 NOTICE.md 中的历史来源与许可说明。

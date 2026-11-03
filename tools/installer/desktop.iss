; Native installer. User projects/configuration live outside {app}.
[Setup]
AppId={{D1041570-17E4-4B55-AD90-3D1B29B30510}}
AppName=Remit
AppVersion={#Version}
AppPublisher=Remit contributors
AppPublisherURL=https://github.com/zhou2030109-glitch/Remit
DefaultDirName={autopf}\Remit
DefaultGroupName=Remit
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
OutputDir={#OutDir}
OutputBaseFilename=Remit-{#Version}-Windows-x64-Setup
SetupIconFile={#Stage}\assets\remit-m-icon.ico
UninstallDisplayIcon={app}\assets\remit-m-icon.ico
LicenseFile={#Stage}\LICENSE
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Remit"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-B ""{app}\tools\desktop_runtime.py"""; IconFilename: "{app}\assets\remit-m-icon.ico"
Name: "{autodesktop}\Remit"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-B ""{app}\tools\desktop_runtime.py"""; IconFilename: "{app}\assets\remit-m-icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-B ""{app}\tools\desktop_runtime.py"""; Description: "打开 Remit"; Flags: nowait postinstall skipifsilent

[Code]
function RemitRunning(): Boolean;
var Code: Integer;
begin
  Result := False;
  if FileExists(ExpandConstant('{app}\tools\desktop_runtime.py')) then
    if Exec(ExpandConstant('{app}\runtime\python\python.exe'),
      '-B "' + ExpandConstant('{app}\tools\desktop_runtime.py') + '" --is-running',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
      Result := Code = 0;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var I: Integer; Directory: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    Directory := ExpandConstant('{app}');
    for I := 1 to Length(Directory) do
      if Ord(Directory[I]) > 127 then begin
        MsgBox('论文编译组件需要英文安装路径。请保留默认目录；项目和用户名可以包含中文。', mbInformation, MB_OK);
        Result := False;
        Exit;
      end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if RemitRunning() then
    Result := '请先从托盘退出 Remit，保留进度后再继续安装。';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer;
begin
  if CurStep = ssPostInstall then begin
    if not Exec(ExpandConstant('{app}\runtime\installers\vc_redist.x64.exe'),
      '/install /quiet /norestart', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('无法安装 Microsoft C++ 运行库。');
    if (Code <> 0) and (Code <> 3010) and (Code <> 1638) then
      RaiseException('Microsoft C++ 运行库安装失败，错误码：' + IntToStr(Code));
    if not Exec(ExpandConstant('{app}\runtime\python\python.exe'),
      '-B "' + ExpandConstant('{app}\tools\desktop_runtime.py') + '" --check',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('无法检查安装文件，请重新安装。');
    if Code <> 0 then RaiseException('安装文件不完整，请重新安装。');
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := not RemitRunning();
  if not Result then MsgBox('请先从托盘退出 Remit，再卸载。项目和设置会保留。', mbInformation, MB_OK);
end;

[Messages]
WelcomeLabel2=Remit 是你的数模小助手。[n][n]已内置 Python、常用计算库、本地存储、文档转换和论文编译环境。无需另装 Python 或 Node。[n][n]首次打开后，填写你自己的模型服务信息。MATLAB 需要另行安装并持有许可证。[n][n]此版本尚未进行发布者签名。项目和设置保存在用户数据目录，升级及卸载均保留。

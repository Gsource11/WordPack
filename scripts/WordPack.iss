#define MyAppName "WordPack"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "WordPack"
#define MyAppExeName "WordPack.exe"

[Setup]
AppId={{A8A3F9BE-DF74-40B2-9A96-3D9BDB7F9C61}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist\installer
OutputBaseFilename=WordPack-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\icon\app-icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=no
ShowLanguageDialog=yes
LanguageDetectionMethod=uilanguage
CloseApplications=no
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "EnglishCustom.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[CustomMessages]
english.WebView2PageTitle=Install WebView2 Runtime
english.WebView2PageSubtitle=WordPack requires WebView2 Runtime to run.
english.WebView2NeedText=WebView2 Runtime is not detected on this system.%nChoose how to continue before installation:
english.WebView2AutoOption=Automatically download and install WebView2 now (Recommended)
english.WebView2ManualOption=Skip for now and install WebView2 manually later
english.WebView2ManualLink=Manual download link:%n%1
english.WebView2ManualChoiceMsg=You chose manual install. WordPack may not start until WebView2 is installed.%n%nDownload link:%n%1
english.WebView2InstallingMsg=Installing WebView2 Runtime silently. This may take up to 2 minutes...
english.WebView2InstallFailedMsg=WebView2 installation did not complete.%n%nYou can continue and install WebView2 later from:%n%1
english.WebView2MissingAfterInstallMsg=WebView2 Runtime is still missing. WordPack may not start correctly yet.%n%nPlease install WebView2 Runtime from:%n%1
english.CreateDesktopIcon=Create a &desktop shortcut
english.LaunchProgram=Launch %1
chinesesimplified.WebView2PageTitle=安装 WebView2 运行时
chinesesimplified.WebView2PageSubtitle=WordPack 运行依赖 WebView2 Runtime。
chinesesimplified.WebView2NeedText=系统中未检测到 WebView2 Runtime。%n请选择安装前的处理方式：
chinesesimplified.WebView2AutoOption=立即自动下载安装 WebView2（推荐）
chinesesimplified.WebView2ManualOption=暂时跳过，稍后手动安装 WebView2
chinesesimplified.WebView2ManualLink=手动下载地址：%n%1
chinesesimplified.WebView2ManualChoiceMsg=你选择了稍后手动安装。在安装 WebView2 之前，WordPack 可能无法启动。%n%n下载地址：%n%1
chinesesimplified.WebView2InstallingMsg=正在静默安装 WebView2 Runtime，最多可能需要 2 分钟，请稍候...
chinesesimplified.WebView2InstallFailedMsg=WebView2 安装未完成。%n%n你可以先继续安装，然后稍后从以下地址安装 WebView2：%n%1
chinesesimplified.WebView2MissingAfterInstallMsg=系统仍缺少 WebView2 Runtime，WordPack 可能暂时无法正常启动。%n%n请从以下地址安装 WebView2：%n%1
chinesesimplified.CreateDesktopIcon=创建桌面快捷方式(&D)
chinesesimplified.LaunchProgram=启动 %1

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\WordPack\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\*"
Type: filesandordirs; Name: "{app}"

[Code]
const
  WebView2BootstrapperUrl = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  WebView2DownloadPage = 'https://developer.microsoft.com/microsoft-edge/webview2/';

var
  WebView2Page: TWizardPage;
  WebView2NeedInstall: Boolean;
  WebView2OptionAuto: TNewRadioButton;
  WebView2OptionManual: TNewRadioButton;
  WebView2StatusText: TNewStaticText;

function HasWebView2Runtime(): Boolean;
var
  VersionValue: String;
begin
  Result := False;
  if RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', VersionValue) and (Trim(VersionValue) <> '') and (Trim(VersionValue) <> '0.0.0.0') then
  begin
    Result := True;
    exit;
  end;
  if RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', VersionValue) and (Trim(VersionValue) <> '') and (Trim(VersionValue) <> '0.0.0.0') then
  begin
    Result := True;
    exit;
  end;
  if IsWin64() and RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', VersionValue) and (Trim(VersionValue) <> '') and (Trim(VersionValue) <> '0.0.0.0') then
  begin
    Result := True;
    exit;
  end;
end;

function WaitForWebView2Runtime(TimeoutSeconds: Integer): Boolean;
var
  Attempt: Integer;
  Attempts: Integer;
begin
  if HasWebView2Runtime() then
  begin
    Result := True;
    exit;
  end;

  if TimeoutSeconds < 1 then
    TimeoutSeconds := 1;

  Attempts := (TimeoutSeconds * 1000) div 500;
  if Attempts < 1 then
    Attempts := 1;
  for Attempt := 1 to Attempts do
  begin
    if HasWebView2Runtime() then
    begin
      Result := True;
      exit;
    end;
    Sleep(500);
  end;

  Result := HasWebView2Runtime();
end;

function TryInstallWebView2OnlineInteractive(): Boolean;
var
  PsExe: String;
  PsPath: String;
  PsScript: String;
  ResultCode: Integer;
begin
  if HasWebView2Runtime() then
  begin
    Result := True;
    exit;
  end;

  PsExe := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  PsPath := ExpandConstant('{tmp}\wordpack-install-webview2.ps1');
  PsScript :=
    '$ErrorActionPreference = ''Stop'''#13#10 +
    '$ProgressPreference = ''SilentlyContinue'''#13#10 +
    '$url = ''' + WebView2BootstrapperUrl + ''''#13#10 +
    '$out = Join-Path $env:TEMP ''MicrosoftEdgeWebView2Setup.exe'''#13#10 +
    'Invoke-WebRequest -Uri $url -OutFile $out'#13#10 +
    '& $out /silent /install'#13#10 +
    'exit $LASTEXITCODE'#13#10;
  SaveStringToFile(PsPath, PsScript, False);

  if not Exec(PsExe, '-NoProfile -ExecutionPolicy Bypass -File "' + PsPath + '"', '', SW_HIDE, ewNoWait, ResultCode) then
  begin
    Result := WaitForWebView2Runtime(120);
    exit;
  end;

  Result := WaitForWebView2Runtime(120);
end;

procedure InitializeWizard();
begin
  WebView2NeedInstall := not HasWebView2Runtime();
  if WebView2NeedInstall then
  begin
    WebView2Page := CreateCustomPage(
      wpSelectTasks,
      CustomMessage('WebView2PageTitle'),
      CustomMessage('WebView2PageSubtitle')
    );
    with TNewStaticText.Create(WizardForm) do
    begin
      Parent := WebView2Page.Surface;
      Left := ScaleX(0);
      Top := ScaleY(8);
      Width := WebView2Page.SurfaceWidth;
      Height := ScaleY(96);
      Caption := CustomMessage('WebView2NeedText');
      AutoSize := False;
      WordWrap := True;
    end;

    WebView2OptionAuto := TNewRadioButton.Create(WizardForm);
    WebView2OptionAuto.Parent := WebView2Page.Surface;
    WebView2OptionAuto.Left := ScaleX(0);
    WebView2OptionAuto.Top := ScaleY(116);
    WebView2OptionAuto.Width := WebView2Page.SurfaceWidth;
    WebView2OptionAuto.Caption := CustomMessage('WebView2AutoOption');
    WebView2OptionAuto.Checked := True;

    WebView2OptionManual := TNewRadioButton.Create(WizardForm);
    WebView2OptionManual.Parent := WebView2Page.Surface;
    WebView2OptionManual.Left := ScaleX(0);
    WebView2OptionManual.Top := ScaleY(142);
    WebView2OptionManual.Width := WebView2Page.SurfaceWidth;
    WebView2OptionManual.Caption := CustomMessage('WebView2ManualOption');

    with TNewStaticText.Create(WizardForm) do
    begin
      Parent := WebView2Page.Surface;
      Left := ScaleX(0);
      Top := ScaleY(172);
      Width := WebView2Page.SurfaceWidth;
      Height := ScaleY(48);
      Caption := FmtMessage(CustomMessage('WebView2ManualLink'), [WebView2DownloadPage]);
      AutoSize := False;
      WordWrap := True;
    end;

    WebView2StatusText := TNewStaticText.Create(WizardForm);
    WebView2StatusText.Parent := WebView2Page.Surface;
    WebView2StatusText.Left := ScaleX(0);
    WebView2StatusText.Top := ScaleY(226);
    WebView2StatusText.Width := WebView2Page.SurfaceWidth;
    WebView2StatusText.Height := ScaleY(28);
    WebView2StatusText.Caption := '';
    WebView2StatusText.AutoSize := False;
    WebView2StatusText.WordWrap := True;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (WebView2NeedInstall) and (Assigned(WebView2Page)) and (PageID = WebView2Page.ID) then
    Result := False;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if WebView2NeedInstall and Assigned(WebView2Page) and (CurPageID = WebView2Page.ID) then
  begin
    if Assigned(WebView2OptionManual) and WebView2OptionManual.Checked then
    begin
      MsgBox(
        FmtMessage(CustomMessage('WebView2ManualChoiceMsg'), [WebView2DownloadPage]),
        mbInformation,
        MB_OK
      );
      exit;
    end;

    if Assigned(WebView2StatusText) then
    begin
      WebView2StatusText.Caption := CustomMessage('WebView2InstallingMsg');
      WebView2StatusText.Repaint();
    end;

    if not TryInstallWebView2OnlineInteractive() and not HasWebView2Runtime() then
    begin
      MsgBox(
        FmtMessage(CustomMessage('WebView2InstallFailedMsg'), [WebView2DownloadPage]),
        mbInformation,
        MB_OK
      );
    end;

    if Assigned(WebView2StatusText) then
      WebView2StatusText.Caption := '';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    { Stop running instance only after user clicked Install. }
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM "{#MyAppExeName}" /F /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  if CurStep = ssPostInstall then
  begin
    if not HasWebView2Runtime() then
    begin
      MsgBox(
        FmtMessage(CustomMessage('WebView2MissingAfterInstallMsg'), [WebView2DownloadPage]),
        mbInformation,
        MB_OK
      );
    end;
  end;
end;

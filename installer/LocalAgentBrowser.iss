; LocalAgentBrowser for Windows. Built by installer\build.ps1, which stages the app with its own Python in {#Stage}.
; Per-user install (no administrator rights), Start-menu and desktop shortcuts, an uninstaller.

#ifndef AppVersion
  #define AppVersion "dev"
#endif

[Setup]
AppId={{6E1E4C2A-5B7D-4D3B-9C61-3F4E2A8B7C10}
AppName=LocalAgentBrowser
AppVersion={#AppVersion}
AppPublisher=LocalAgentBrowser project
AppPublisherURL=https://snowfxxx.github.io/LocalAgentBrowser/
AppSupportURL=https://github.com/SnowFXXX/LocalAgentBrowser/issues
DefaultDirName={localappdata}\Programs\LocalAgentBrowser
DefaultGroupName=LocalAgentBrowser
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=LocalAgentBrowser-{#AppVersion}-setup
SetupIconFile={#Stage}\tools\windows\localagentbrowser.ico
UninstallDisplayIcon={app}\tools\windows\localagentbrowser.ico
Compression=lzma2/max
SolidCompression=yes
; Always dark, in the project website's colors (navy #0b111b), in Setup and Uninstall alike
WizardStyle=modern dark windows11 includetitlebar hidebevels
WizardBackColor=#0b111b
WizardImageBackColor=#0b111b
WizardImageFile=images\wizard-100.bmp,images\wizard-150.bmp,images\wizard-200.bmp
WizardSmallImageFile=images\small-100.bmp,images\small-150.bmp,images\small-200.bmp
; The uninstaller in its own clearly named folder, not "unins000.exe" beside the app's files
UninstallFilesDir={app}\uninstall
LicenseFile={#Stage}\LICENSE
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; One plain list of checkboxes, no "setup type" to pick
[Types]
Name: "selection"; Description: "Your selection"; Flags: iscustom

[Components]
Name: "app"; Description: "LocalAgentBrowser (the agent, its launcher and shortcuts)"; Types: selection; Flags: fixed
Name: "suite"; Description: "Test suite (the 7-task regression suite the project is measured with; runs take hours)"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Excludes: "tools\regression.py,tools\live_test.py,tools\windows\run_suite.cmd"; Flags: recursesubdirs ignoreversion; Components: app
Source: "{#Stage}\tools\regression.py"; DestDir: "{app}\tools"; Flags: ignoreversion; Components: suite
Source: "{#Stage}\tools\live_test.py"; DestDir: "{app}\tools"; Flags: ignoreversion; Components: suite
Source: "{#Stage}\tools\windows\run_suite.cmd"; DestDir: "{app}\tools\windows"; Flags: ignoreversion; Components: suite

[Icons]
; The launcher starts in Documents, not in the program folder: a launcher window left open then doesn't keep the
; program folder from being removed by the uninstaller
Name: "{group}\LocalAgentBrowser"; Filename: "{app}\tools\windows\LocalAgentBrowser.cmd"; WorkingDir: "{userdocs}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Comment: "Research an objective with a local model"
Name: "{autodesktop}\LocalAgentBrowser"; Filename: "{app}\tools\windows\LocalAgentBrowser.cmd"; WorkingDir: "{userdocs}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Comment: "Research an objective with a local model"; Tasks: desktopicon
Name: "{group}\Past reports"; Filename: "{userdocs}\LocalAgentBrowser runs"
Name: "{group}\Run the test suite"; Filename: "{app}\tools\windows\run_suite.cmd"; WorkingDir: "{userdocs}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Components: suite
; The uninstaller restarts itself from a temporary copy, and that copy may not be allowed to come to the front: its
; "Are you sure" box then opened behind other windows, minimized. From this shortcut it runs without its own boxes
; (/SILENT) and asks with boxes that always open on top (see InitializeUninstall)
Name: "{group}\Uninstall LocalAgentBrowser"; Filename: "{uninstallexe}"; Parameters: "/SILENT /FROMSHORTCUT"

[Dirs]
Name: "{userdocs}\LocalAgentBrowser runs"; Flags: uninsneveruninstall

[Run]
; The downloads run without a console window of their own; setup's progress page shows what is happening
Filename: "{app}\python\python.exe"; Parameters: "-m playwright install chromium"; StatusMsg: "Downloading the Chromium browser the agent uses (about 150 MB, once)..."; Flags: waituntilterminated runhidden
Filename: "{code:LmsPath}"; Parameters: "get {code:ModelDownload} -y"; StatusMsg: "Downloading the chosen model through LM Studio (several GB; this can take a while)..."; Check: WantsModelDownload; Flags: waituntilterminated runhidden
Filename: "{app}\tools\windows\LocalAgentBrowser.cmd"; WorkingDir: "{userdocs}"; Description: "Start LocalAgentBrowser now"; Flags: postinstall nowait skipifsilent shellexec unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}\settings"
Type: filesandordirs; Name: "{app}\python\Lib\site-packages\__pycache__"

[Code]
const
  ModelCount = 4;
  CloudModel = 'microsoft/phi-4';
var
  ModelPage: TInputOptionWizardPage;
  OptionalPage: TInputOptionWizardPage;
  ModelIds, ModelDownloads: array[0..ModelCount] of String;

{ Uninstall from the Start-menu shortcut: the same questions as the uninstaller's own, in boxes that open on top }
function MessageBoxW(hWnd: Integer; lpText, lpCaption: String; uType: Cardinal): Integer;
  external 'MessageBoxW@user32.dll stdcall';

const
  { Windows MessageBox flags, named apart from Inno Setup's own MsgBox constants }
  WinOk = $0; WinYesNo = $4; WinQuestion = $20; WinInformation = $40; WinSecondDefault = $100;
  WinForeground = $10000; WinTopmost = $40000; WinYes = 6;
  UninstallTitle = 'LocalAgentBrowser Uninstall';

function FromShortcut: Boolean;
begin
  Result := Pos('/FROMSHORTCUT', Uppercase(GetCmdTail)) > 0;
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  if FromShortcut then
    Result := MessageBoxW(0, 'Are you sure you want to completely remove LocalAgentBrowser and all of its components?',
      UninstallTitle, WinYesNo or WinQuestion or WinSecondDefault or WinForeground or WinTopmost) = WinYes;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usDone) and FromShortcut then
    MessageBoxW(0, 'LocalAgentBrowser was successfully removed from your computer.', UninstallTitle,
      WinOk or WinInformation or WinForeground or WinTopmost);
end;

function LmsPath(Param: String): String;
begin
  Result := ExpandConstant('{%USERPROFILE}\.lmstudio\bin\lms.exe');
end;

function ChosenModel: Integer;
var
  I: Integer;
begin
  Result := ModelCount; { the last option: choose a model myself }
  for I := 0 to ModelCount do
    if ModelPage.Values[I] then
      Result := I;
end;

function ModelDownload(Param: String): String;
begin
  Result := ModelDownloads[ChosenModel];
end;

function WantsModelDownload: Boolean;
begin
  { Never in a silent install: a model is several GB, and silent installs are for testing or for admins }
  Result := (ChosenModel < ModelCount) and (not OptionalPage.Values[0]) and FileExists(LmsPath('')) and (not WizardSilent);
end;

procedure InitializeWizard;
begin
  { Tested on the release's own suite (7 tasks, 90 minutes each, on a 6-core CPU with no GPU) }
  ModelIds[0] := 'qwen2.5-7b-instruct';        ModelDownloads[0] := 'https://huggingface.co/lmstudio-community/Qwen2.5-7B-Instruct-GGUF@Q4_K_M';
  ModelIds[1] := 'meta-llama-3.1-8b-instruct'; ModelDownloads[1] := 'https://huggingface.co/lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF@Q4_K_M';
  ModelIds[2] := 'qwen2.5-14b-instruct';       ModelDownloads[2] := 'https://huggingface.co/lmstudio-community/Qwen2.5-14B-Instruct-GGUF@Q4_K_M';
  ModelIds[3] := 'qwen2.5-3b-instruct';        ModelDownloads[3] := 'https://huggingface.co/lmstudio-community/Qwen2.5-3B-Instruct-GGUF@Q4_K_M';
  ModelIds[4] := '';                           ModelDownloads[4] := '';

  ModelPage := CreateInputOptionPage(wpSelectComponents, 'Choose a model',
    'Pick one of the models LocalAgentBrowser was tested with. You can use any other model later.',
    'LocalAgentBrowser runs on a model in LM Studio (lmstudio.ai). If LM Studio is installed, setup downloads the ' +
    'model you pick through it. Results are from the project''s 7-task test suite on an ordinary 6-core PC with no GPU.',
    True, False);
  ModelPage.Add('Qwen2.5 7B Instruct (recommended): 4.7 GB, 8 GB RAM; finished 6 of 7 tasks');
  ModelPage.Add('Llama 3.1 8B Instruct: 4.9 GB, 8 GB RAM; finished 7 of 7 tasks');
  ModelPage.Add('Qwen2.5 14B Instruct: 9.0 GB, 16 GB RAM; finished 5 of 7, about 2.5 times slower');
  ModelPage.Add('Qwen2.5 3B Instruct: 1.9 GB, 4 GB RAM; finished 3 of 7, for small PCs');
  ModelPage.Add('None now: I''ll choose a model in LM Studio myself');
  ModelPage.Values[0] := True;

  OptionalPage := CreateInputOptionPage(ModelPage.ID, 'Optional', 'Off unless you turn it on.',
    'Use cloud models instead of LM Studio, through your own OpenRouter account. The objective, the prompts and ' +
    'the pages the agent reads are then sent to OpenRouter and the model''s provider, and each run costs money on ' +
    'your account. You set your key yourself in the OPENROUTER_API_KEY environment variable; setup never asks for it. ' +
    'Runs are attributed to LocalAgentBrowser in OpenRouter''s app listings.',
    False, False);
  OptionalPage.Add('Use cloud models (OpenRouter, your own key)');
  OptionalPage.Values[0] := False;
end;

{ Adds one section to the summary, with a blank line between sections and none before the first }
procedure AddSection(var Memo: String; Section, NewLine: String);
begin
  if Section = '' then
    Exit;
  if Memo <> '' then
    Memo := Memo + NewLine + NewLine;
  Memo := Memo + Section;
end;

{ The "Ready to Install" summary: what was actually chosen, without a "setup type" line. With cloud models on, it
  names the cloud model the runs will use, and says the LM Studio model is for when cloud models are turned off }
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
var
  Model, ModelTitle, Cloud: String;
begin
  if ChosenModel < ModelCount then
    Model := ModelPage.CheckListBox.ItemCaption[ChosenModel]
  else
    Model := 'None now: chosen later in LM Studio';
  if OptionalPage.Values[0] then
  begin
    ModelTitle := 'Model in LM Studio (used when cloud models are turned off):';
    Cloud := 'On: OpenRouter''s ' + CloudModel + ', with your own key'
  end
  else
  begin
    ModelTitle := 'Model:';
    Cloud := 'Off';
  end;
  Result := '';
  AddSection(Result, MemoDirInfo, NewLine);
  AddSection(Result, MemoComponentsInfo, NewLine);
  AddSection(Result, ModelTitle + NewLine + Space + Model, NewLine);
  AddSection(Result, 'Cloud models:' + NewLine + Space + Cloud, NewLine);
  AddSection(Result, MemoTasksInfo, NewLine);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Dir: String;
  Lines: TArrayOfString;
begin
  if CurStep <> ssPostInstall then
    Exit;
  Dir := ExpandConstant('{app}\settings');
  ForceDirectories(Dir);
  DeleteFile(Dir + '\model.cmd');
  DeleteFile(Dir + '\cloud.cmd');
  if ModelIds[ChosenModel] <> '' then
  begin
    SetArrayLength(Lines, 2);
    Lines[0] := 'rem Written by setup: the model chosen at install';
    Lines[1] := 'set "LMSTUDIO_MODEL=' + ModelIds[ChosenModel] + '"';
    SaveStringsToFile(Dir + '\model.cmd', Lines, False);
  end;
  if OptionalPage.Values[0] then
  begin
    SetArrayLength(Lines, 8);
    Lines[0] := 'rem Written by setup: cloud models through OpenRouter, chosen at install. The key comes from your own';
    Lines[1] := 'rem OPENROUTER_API_KEY environment variable and is never stored here. Delete this file to use LM Studio again.';
    Lines[2] := 'set "LAB_CLOUD=1"';
    Lines[3] := 'set "LMSTUDIO_BASE_URL=https://openrouter.ai/api/v1"';
    Lines[4] := 'set "LMSTUDIO_MODEL=' + CloudModel + '"';
    Lines[5] := 'set "LMSTUDIO_EXTRA_BODY={"provider": {"require_parameters": true, "data_collection": "deny"}}"';
    Lines[6] := 'rem OpenRouter app attribution (lists runs under LocalAgentBrowser, no content shared); delete the next line to turn it off';
    Lines[7] := 'set "LMSTUDIO_EXTRA_HEADERS={"HTTP-Referer": "https://snowfxxx.github.io/LocalAgentBrowser/", "X-OpenRouter-Title": "LocalAgentBrowser"}"';
    SaveStringsToFile(Dir + '\cloud.cmd', Lines, False);
  end;
end;

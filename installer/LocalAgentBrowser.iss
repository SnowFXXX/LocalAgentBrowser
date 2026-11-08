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
Name: "{group}\LocalAgentBrowser"; Filename: "{app}\tools\windows\LocalAgentBrowser.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Comment: "Research an objective with a local model"
Name: "{autodesktop}\LocalAgentBrowser"; Filename: "{app}\tools\windows\LocalAgentBrowser.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Comment: "Research an objective with a local model"; Tasks: desktopicon
Name: "{group}\Past reports"; Filename: "{userdocs}\LocalAgentBrowser runs"
Name: "{group}\Run the test suite"; Filename: "{app}\tools\windows\run_suite.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Components: suite
Name: "{group}\Uninstall LocalAgentBrowser"; Filename: "{uninstallexe}"

[Dirs]
Name: "{userdocs}\LocalAgentBrowser runs"; Flags: uninsneveruninstall

[Run]
Filename: "{app}\python\python.exe"; Parameters: "-m playwright install chromium"; StatusMsg: "Downloading the Chromium browser the agent uses (about 150 MB, once)..."; Flags: waituntilterminated
Filename: "{code:LmsPath}"; Parameters: "get {code:ModelDownload} -y"; StatusMsg: "Downloading the chosen model through LM Studio (several GB; this can take a while)..."; Check: WantsModelDownload; Flags: waituntilterminated
Filename: "{app}\tools\windows\LocalAgentBrowser.cmd"; Description: "Start LocalAgentBrowser now"; Flags: postinstall nowait skipifsilent shellexec unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}\settings"
Type: filesandordirs; Name: "{app}\python\Lib\site-packages\__pycache__"

[Code]
const
  ModelCount = 4;
var
  ModelPage: TInputOptionWizardPage;
  OptionalPage: TInputOptionWizardPage;
  ModelIds, ModelDownloads: array[0..ModelCount] of String;

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

{ The "Ready to Install" summary: what was actually chosen, without a "setup type" line }
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
var
  Model, Cloud: String;
begin
  if ChosenModel < ModelCount then
    Model := ModelPage.CheckListBox.ItemCaption[ChosenModel]
  else
    Model := 'None now: chosen later in LM Studio';
  if OptionalPage.Values[0] then
    Cloud := 'On: OpenRouter, with your own key'
  else
    Cloud := 'Off';
  Result := MemoDirInfo + NewLine + NewLine + MemoComponentsInfo + NewLine + NewLine +
    'Model:' + NewLine + Space + Model + NewLine + NewLine +
    'Cloud models:' + NewLine + Space + Cloud;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + NewLine + MemoTasksInfo;
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
    SetArrayLength(Lines, 6);
    Lines[0] := 'rem Written by setup: cloud models through OpenRouter, chosen at install. The key comes from your own';
    Lines[1] := 'rem OPENROUTER_API_KEY environment variable and is never stored here. Delete this file to use LM Studio again.';
    Lines[2] := 'set "LAB_CLOUD=1"';
    Lines[3] := 'set "LMSTUDIO_BASE_URL=https://openrouter.ai/api/v1"';
    Lines[4] := 'set "LMSTUDIO_MODEL=qwen/qwen-2.5-7b-instruct"';
    Lines[5] := 'set "LMSTUDIO_EXTRA_BODY={"provider": {"require_parameters": true, "data_collection": "deny"}}"';
    SaveStringsToFile(Dir + '\cloud.cmd', Lines, False);
  end;
end;

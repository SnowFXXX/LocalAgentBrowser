; LocalAgentBrowser for Windows. Built by installer\build.ps1, which stages the app with its own Python in {#Stage}.
; Per-user install (no administrator rights), Start-menu and desktop shortcuts, an uninstaller.

#ifndef AppVersion
  #define AppVersion "dev"
#endif
; Clean Room, a private edition (build.ps1 -CleanRoom, which defines CleanRoom): the cloud code left out of the app
; itself (tools\clean_room.py) and none of this script's cloud parts. It has its own AppId, folder and shortcuts, so
; it installs beside the full edition.
#ifdef CleanRoom
  #define AppTitle "LocalAgentBrowser Clean Room"
  #define AppGuid "{{F102325C-1094-4DDD-82BC-C7CF81E80928}"
  #define FileTitle "LocalAgentBrowser-CleanRoom"
#else
  #define AppTitle "LocalAgentBrowser"
  #define AppGuid "{{6E1E4C2A-5B7D-4D3B-9C61-3F4E2A8B7C10}"
  #define FileTitle "LocalAgentBrowser"
#endif

[Setup]
AppId={#AppGuid}
AppName={#AppTitle}
AppVersion={#AppVersion}
AppVerName={#AppTitle} {#AppVersion}
AppPublisher=LocalAgentBrowser project
AppPublisherURL=https://snowfxxx.github.io/LocalAgentBrowser/
AppSupportURL=https://github.com/SnowFXXX/LocalAgentBrowser/issues
DefaultDirName={localappdata}\Programs\{#AppTitle}
DefaultGroupName={#AppTitle}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename={#FileTitle}-{#AppVersion}-setup
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
Name: "app"; Description: "{#AppTitle} (the agent, its launcher and shortcuts)"; Types: selection; Flags: fixed
Name: "suite"; Description: "Test suite (the 7-task regression suite the project is measured with; runs take hours)"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Excludes: "tools\regression.py,tools\live_test.py,tools\windows\run_suite.cmd"; Flags: recursesubdirs ignoreversion; Components: app
Source: "{#Stage}\tools\regression.py"; DestDir: "{app}\tools"; Flags: ignoreversion; Components: suite
Source: "{#Stage}\tools\live_test.py"; DestDir: "{app}\tools"; Flags: ignoreversion; Components: suite
Source: "{#Stage}\tools\windows\run_suite.cmd"; DestDir: "{app}\tools\windows"; Flags: ignoreversion; Components: suite

[Icons]
; The shortcuts open the Windows UI (pythonw: no console window), which opens classic mode instead when that is the
; saved choice. They start in Documents, not in the program folder: a window left open then doesn't keep the program
; folder from being removed by the uninstaller
Name: "{group}\{#AppTitle}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m localagentbrowser.winui"; WorkingDir: "{userdocs}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Comment: "Research an objective with a local model"
Name: "{autodesktop}\{#AppTitle}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m localagentbrowser.winui"; WorkingDir: "{userdocs}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Comment: "Research an objective with a local model"; Tasks: desktopicon
; Classic mode, the console, without changing the saved choice
Name: "{group}\{#AppTitle} (classic console)"; Filename: "{app}\tools\windows\LocalAgentBrowser.cmd"; WorkingDir: "{userdocs}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Comment: "Research an objective in a console window (classic mode)"
#ifndef CleanRoom
; With cloud models chosen, a second shortcut beside the local one: the plain shortcut starts with LM Studio's model,
; this one with the cloud model ticked (the UI's "--remote"; in classic mode, the launcher's "cloud" argument)
Name: "{group}\{#AppTitle} (cloud)"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m localagentbrowser.winui --remote"; WorkingDir: "{userdocs}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Comment: "Research an objective with a cloud model through OpenRouter (your own key)"; Check: CloudChosen
Name: "{autodesktop}\{#AppTitle} (cloud)"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m localagentbrowser.winui --remote"; WorkingDir: "{userdocs}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Comment: "Research an objective with a cloud model through OpenRouter (your own key)"; Tasks: desktopicon; Check: CloudChosen
#endif
Name: "{group}\Past reports"; Filename: "{userdocs}\LocalAgentBrowser runs"
Name: "{group}\Run the test suite"; Filename: "{app}\tools\windows\run_suite.cmd"; WorkingDir: "{userdocs}"; IconFilename: "{app}\tools\windows\localagentbrowser.ico"; Components: suite
; The uninstaller restarts itself from a temporary copy, and that copy may not be allowed to come to the front: its
; "Are you sure" box then opened behind other windows, minimized. From this shortcut it runs without its own boxes
; (/SILENT) and asks with boxes that always open on top (see InitializeUninstall)
Name: "{group}\Uninstall {#AppTitle}"; Filename: "{uninstallexe}"; Parameters: "/SILENT /FROMSHORTCUT"

#ifndef CleanRoom
[InstallDelete]
; An earlier install's cloud shortcuts go, so they come back only when cloud models are chosen again
Type: files; Name: "{group}\{#AppTitle} (cloud).lnk"
Type: files; Name: "{autodesktop}\{#AppTitle} (cloud).lnk"
#endif

[Dirs]
Name: "{userdocs}\LocalAgentBrowser runs"; Flags: uninsneveruninstall

[Run]
; The downloads run without a console window of their own; setup's progress page shows what is happening
Filename: "{app}\python\python.exe"; Parameters: "-m playwright install chromium"; StatusMsg: "Downloading the Chromium browser the agent uses (about 150 MB, once)..."; Flags: waituntilterminated runhidden
Filename: "{code:LmsPath}"; Parameters: "get {code:ModelDownload} -y"; StatusMsg: "Downloading the chosen model through LM Studio (several GB; this can take a while)..."; Check: WantsModelDownload; Flags: waituntilterminated runhidden
#ifdef CleanRoom
Filename: "{app}\python\pythonw.exe"; Parameters: "-m localagentbrowser.winui"; WorkingDir: "{userdocs}"; Description: "Start {#AppTitle} now"; Flags: postinstall nowait skipifsilent unchecked
#else
; With cloud models chosen, "Start now" opens the UI the way the cloud shortcut does
Filename: "{app}\python\pythonw.exe"; Parameters: "-m localagentbrowser.winui {code:StartParameters}"; WorkingDir: "{userdocs}"; Description: "Start {#AppTitle} now"; Flags: postinstall nowait skipifsilent unchecked
#endif

[UninstallDelete]
; Setup's own folders, whole: Python writes a __pycache__ folder beside every module it runs, and the uninstaller
; removes only the files it installed, so a BB3 test (2026-11-17) left python\Lib\site-packages\*\__pycache__ and
; localagentbrowser\__pycache__ behind, and the next install found the folder already there. Nothing of the
; user's is kept in these: settings is setup's, and reports go to Documents
Type: filesandordirs; Name: "{app}\settings"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\localagentbrowser"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\__pycache__"
; The Windows UI's saved choices (mode, model, time limit, reports folder; localagentbrowser\settings.py). Reports
; stay in Documents
Type: filesandordirs; Name: "{userappdata}\LocalAgentBrowser"

[Code]
const
  ModelCount = 5;
#ifndef CleanRoom
  CloudModel = 'microsoft/phi-4';
#endif
var
  ModelPage: TInputOptionWizardPage;
#ifndef CleanRoom
  OptionalPage: TInputOptionWizardPage;
#endif
  ModelIds, ModelDownloads: array[0..ModelCount] of String;
  { An update starts from the earlier install's choices, read once from its settings folder }
  Prefilled: Boolean;
  { A model the earlier install used that isn't on the list (set by hand): kept unless another is chosen }
  KeptModel: String;

{ Uninstall from the Start-menu shortcut: the same questions as the uninstaller's own, in boxes that open on top }
function MessageBoxW(hWnd: Integer; lpText, lpCaption: String; uType: Cardinal): Integer;
  external 'MessageBoxW@user32.dll stdcall';

const
  { Windows MessageBox flags, named apart from Inno Setup's own MsgBox constants }
  WinOk = $0; WinYesNo = $4; WinQuestion = $20; WinInformation = $40; WinSecondDefault = $100;
  WinForeground = $10000; WinTopmost = $40000; WinYes = 6;
  UninstallTitle = '{#AppTitle} Uninstall';

function FromShortcut: Boolean;
begin
  Result := Pos('/FROMSHORTCUT', Uppercase(GetCmdTail)) > 0;
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  if FromShortcut then
    Result := MessageBoxW(0, 'Are you sure you want to completely remove {#AppTitle} and all of its components?',
      UninstallTitle, WinYesNo or WinQuestion or WinSecondDefault or WinForeground or WinTopmost) = WinYes;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usDone) and FromShortcut then
    MessageBoxW(0, '{#AppTitle} was successfully removed from your computer.', UninstallTitle,
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

{ The value a settings file sets for Name (a line: set "Name=value"), or '' }
function SettingFromFile(FileName, Name: String): String;
var
  Lines: TArrayOfString;
  I: Integer;
  S, Start: String;
begin
  Result := '';
  if not LoadStringsFromFile(FileName, Lines) then
    Exit;
  Start := 'set "' + Name + '=';
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    S := Trim(Lines[I]);
    if Pos(Start, S) = 1 then
    begin
      Delete(S, 1, Length(Start));
      if (Length(S) > 0) and (S[Length(S)] = '"') then
        Delete(S, Length(S), 1);
      Result := S;
    end;
  end;
end;

{ Updating: the pages start from what the earlier install chose, read from its settings folder, so clicking Next
  keeps it. 1.67 always started from the defaults and replaced the settings, so an update on BB3 (2026-11-18)
  switched off the earlier install's choices. A fresh install has no settings folder and keeps the defaults. }
procedure PrefillFromEarlierInstall;
var
  Dir, Id: String;
  I, Found: Integer;
begin
  Prefilled := True;
  Dir := WizardDirValue + '\settings';
  if not DirExists(Dir) then
    Exit;
  Id := SettingFromFile(Dir + '\model.cmd', 'LMSTUDIO_MODEL');
  Found := ModelCount; { no model.cmd: the earlier install chose its model in LM Studio }
  for I := 0 to ModelCount - 1 do
    if CompareText(ModelIds[I], Id) = 0 then
      Found := I;
  if (Id <> '') and (Found = ModelCount) then
    KeptModel := Id;
  ModelPage.Values[Found] := True;
#ifndef CleanRoom
  OptionalPage.Values[0] := FileExists(Dir + '\cloud.cmd');
#endif
end;

function WantsModelDownload: Boolean;
begin
  { Never in a silent install: a model is several GB, and silent installs are for testing or for admins }
  { Whatever else is chosen: the LocalAgentBrowser shortcut always uses LM Studio }
  Result := (ChosenModel < ModelCount) and FileExists(LmsPath('')) and (not WizardSilent);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = ModelPage.ID) and not Prefilled then
    PrefillFromEarlierInstall;
  { LM Studio may go on downloading the model after setup hands it over }
  if (CurPageID = wpFinished) and WantsModelDownload then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 +
      'The model may still be downloading in LM Studio. If it is, let the download finish there before your first run.';
end;

#ifndef CleanRoom
function CloudChosen: Boolean;
begin
  Result := OptionalPage.Values[0];
end;

function StartParameters(Param: String): String;
begin
  if CloudChosen then
    Result := '--remote'
  else
    Result := '';
end;

#endif
procedure InitializeWizard;
begin
  { Tested on the release's own suite (7 tasks, 90 minutes each, on a 6-core CPU with no GPU) }
  ModelIds[0] := 'qwen2.5-7b-instruct';        ModelDownloads[0] := 'https://huggingface.co/lmstudio-community/Qwen2.5-7B-Instruct-GGUF@Q4_K_M';
  ModelIds[1] := 'meta-llama-3.1-8b-instruct'; ModelDownloads[1] := 'https://huggingface.co/lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF@Q4_K_M';
  ModelIds[2] := 'qwen2.5-14b-instruct';       ModelDownloads[2] := 'https://huggingface.co/lmstudio-community/Qwen2.5-14B-Instruct-GGUF@Q4_K_M';
  ModelIds[3] := 'gemma-3-4b-it';              ModelDownloads[3] := 'https://huggingface.co/lmstudio-community/gemma-3-4b-it-GGUF@Q4_K_M';
  ModelIds[4] := 'qwen2.5-3b-instruct';        ModelDownloads[4] := 'https://huggingface.co/lmstudio-community/Qwen2.5-3B-Instruct-GGUF@Q4_K_M';
  ModelIds[5] := '';                           ModelDownloads[5] := '';

  ModelPage := CreateInputOptionPage(wpSelectComponents, 'Choose a model',
    'Pick one of the models LocalAgentBrowser was tested with. You can use any other model later.',
    'LocalAgentBrowser runs on a model in LM Studio (lmstudio.ai). If LM Studio is installed, setup downloads the ' +
    'model you pick through it. Results are from the project''s 7-task test suite on an ordinary 6-core PC with no GPU.',
    True, False);
  ModelPage.Add('Qwen2.5 7B Instruct (recommended): 4.7 GB, 8 GB RAM; finished 7 of 7 tasks (on 1.67)');
  ModelPage.Add('Llama 3.1 8B Instruct: 4.9 GB, 8 GB RAM; finished 7 of 7 tasks');
  ModelPage.Add('Qwen2.5 14B Instruct: 9.0 GB, 16 GB RAM; finished 5 of 7, about 2.5 times slower');
  ModelPage.Add('Gemma 3 4B Instruct: 3.3 GB, 6 GB RAM; finished 6 of 7 tasks (on 1.66)');
  ModelPage.Add('Qwen2.5 3B Instruct: 1.9 GB, 4 GB RAM; finished 3 of 7, for small PCs');
  ModelPage.Add('None now: I''ll choose a model in LM Studio myself');
  ModelPage.Values[0] := True;
#ifndef CleanRoom

  OptionalPage := CreateInputOptionPage(ModelPage.ID, 'Optional', 'Off unless you turn it on.',
    'Also use cloud models, through your own OpenRouter account, from a second shortcut: LocalAgentBrowser (cloud). ' +
    'The LocalAgentBrowser shortcut always uses LM Studio. Runs from the (cloud) shortcut send the objective, the ' +
    'prompts and the pages the agent reads to OpenRouter and the model''s provider, and each costs money on ' +
    'your account. You set your key yourself in the OPENROUTER_API_KEY environment variable; setup never asks for it. ' +
    'Runs are attributed to LocalAgentBrowser in OpenRouter''s app listings.',
    False, False);
  OptionalPage.Add('Use cloud models (OpenRouter, your own key)');
  OptionalPage.Values[0] := False;
#endif
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

{ The "Ready to Install" summary: what was actually chosen, without a "setup type" line }
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
var
  Model, ModelTitle: String;
begin
  if ChosenModel < ModelCount then
    Model := ModelPage.CheckListBox.ItemCaption[ChosenModel]
  else if KeptModel <> '' then
    Model := 'Kept from the earlier install: ' + KeptModel
  else
    Model := 'None now: chosen later in LM Studio';
  ModelTitle := 'Model:';
#ifndef CleanRoom
  { With cloud models on, the summary says which shortcut uses which model }
  if OptionalPage.Values[0] then
    ModelTitle := 'Model in LM Studio (the LocalAgentBrowser shortcut):';
#endif
  Result := '';
  AddSection(Result, MemoDirInfo, NewLine);
  AddSection(Result, MemoComponentsInfo, NewLine);
  AddSection(Result, ModelTitle + NewLine + Space + Model, NewLine);
#ifndef CleanRoom
  if OptionalPage.Values[0] then
    AddSection(Result, 'Cloud models:' + NewLine + Space + 'On: ' + CloudModel + ' via OpenRouter, your own key, from the (cloud) shortcut', NewLine)
  else
    AddSection(Result, 'Cloud models:' + NewLine + Space + 'Off', NewLine);
#endif
  AddSection(Result, MemoTasksInfo, NewLine);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Dir: String;
  Lines: TArrayOfString;
begin
  if CurStep <> ssPostInstall then
    Exit;
  { The choices of an earlier install are replaced by this one's }
  Dir := ExpandConstant('{app}\settings');
  DelTree(Dir, True, True, True);
  ForceDirectories(Dir);
  if ModelIds[ChosenModel] <> '' then
  begin
    SetArrayLength(Lines, 2);
    Lines[0] := 'rem Written by setup: the model chosen at install';
    Lines[1] := 'set "LMSTUDIO_MODEL=' + ModelIds[ChosenModel] + '"';
    SaveStringsToFile(Dir + '\model.cmd', Lines, False);
  end
  else if KeptModel <> '' then
  begin
    SetArrayLength(Lines, 2);
    Lines[0] := 'rem Written by setup: the model the earlier install used, kept';
    Lines[1] := 'set "LMSTUDIO_MODEL=' + KeptModel + '"';
    SaveStringsToFile(Dir + '\model.cmd', Lines, False);
  end;
#ifndef CleanRoom
  if OptionalPage.Values[0] then
  begin
    SetArrayLength(Lines, 8);
    Lines[0] := 'rem Written by setup: cloud models through OpenRouter, chosen at install. The key comes from your own';
    Lines[1] := 'rem OPENROUTER_API_KEY environment variable and is never stored here. Used only by the (cloud) shortcut.';
    Lines[2] := 'set "LAB_CLOUD=1"';
    Lines[3] := 'set "LMSTUDIO_BASE_URL=https://openrouter.ai/api/v1"';
    Lines[4] := 'set "LMSTUDIO_MODEL=' + CloudModel + '"';
    Lines[5] := 'set "LMSTUDIO_EXTRA_BODY={"provider": {"require_parameters": true, "data_collection": "deny"}}"';
    Lines[6] := 'rem OpenRouter app attribution (lists runs under LocalAgentBrowser, no content shared); delete the next line to turn it off';
    Lines[7] := 'set "LMSTUDIO_EXTRA_HEADERS={"HTTP-Referer": "https://snowfxxx.github.io/LocalAgentBrowser/", "X-OpenRouter-Title": "LocalAgentBrowser"}"';
    SaveStringsToFile(Dir + '\cloud.cmd', Lines, False);
  end;
#endif
end;

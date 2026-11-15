#ifndef PayloadRoot
  #error PayloadRoot required
#endif
#if AppRole == "server"
  #define RoleName "Server"
#else
  #if AppRole != "laser_station"
    #error Unsupported fixed build role
  #endif
  #define RoleName "Laserstation"
#endif
[Setup]
AppId=Makervaerkstedet-{#AppRole}
AppName=Makerværkstedet {#RoleName}
AppVersion={#ProductVersion}
VersionInfoVersion={#WindowsVersion}
DefaultDirName={tmp}\MakervaerkstedetSetup
DisableDirPage=yes
DisableProgramGroupPage=yes
Uninstallable=no
#if AppRole == "server"
PrivilegesRequired=admin
#else
PrivilegesRequired=lowest
#endif
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputRoot}
OutputBaseFilename={#OutputName}
Compression=lzma2
SolidCompression=yes
SetupLogging=yes
WizardStyle=modern dynamic
WizardSizePercent=120,120
[Languages]
Name: "danish"; MessagesFile: "compiler:Languages\Danish.isl"
[LangOptions]
DialogFontName=Segoe UI
DialogFontSize=10
[Files]
Source: "{#PayloadRoot}\*"; DestDir: "{tmp}\mkv-payload"; Flags: recursesubdirs createallsubdirs deleteafterinstall
[Code]
var ExistingInstallation: Boolean;
var InfoPage: TOutputMsgWizardPage;
var NativeAttempted, NativeSucceeded: Boolean;
var FailureReason, ConnectionExportPath: String;
var OpenButton, LaserButton, ConnectionButton, LogButton: TNewButton;
var LogPathEdit: TNewEdit;
#if AppRole == "server"
var SchoolPage: TInputQueryWizardPage;
var RolePage, ThemePage, MachinePage: TInputOptionWizardPage;
#else
var ConnectionPage: TInputFileWizardPage;
#endif
function ValidIPv4(Value: String; Unicast: Boolean): Boolean;
var Part, Count, N, First: Integer; Token: String;
begin
  Result := False; Count := 0; First := -1;
  while Value <> '' do begin
    Part := Pos('.', Value);
    if Part = 0 then begin Token := Value; Value := ''; end
    else begin
      Token := Copy(Value, 1, Part - 1); Delete(Value, 1, Part);
      if Value = '' then Exit;
    end;
    if (Length(Token) = 0) or (Length(Token) > 3) then Exit;
    if (Length(Token) > 1) and (Token[1] = '0') then Exit;
    for N := 1 to Length(Token) do
      if (Token[N] < '0') or (Token[N] > '9') then Exit;
    N := StrToIntDef(Token, -1);
    if (N < 0) or (N > 255) then Exit;
    if Count = 0 then First := N;
    Count := Count + 1;
  end;
  Result := Count = 4;
  if Unicast then Result := Result and (First > 0) and (First < 224) and (First <> 127);
end;
function ValidSubnet(Value: String): Boolean;
var Slash, Prefix, I: Integer; Network, Mask: String;
begin
  Result := False; Slash := Pos('/', Value);
  if Slash = 0 then Exit;
  Network := Copy(Value, 1, Slash - 1); Mask := Copy(Value, Slash + 1, Length(Value));
  if not ValidIPv4(Network, True) or (Mask = '') or (Length(Mask) > 2) then Exit;
  for I := 1 to Length(Mask) do if (Mask[I] < '0') or (Mask[I] > '9') then Exit;
  Prefix := StrToIntDef(Mask, -1);
  Result := (Prefix >= 1) and (Prefix <= 32);
end;
function JSONString(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  StringChangeEx(Value, #13, '\r', True);
  StringChangeEx(Value, #10, '\n', True);
  StringChangeEx(Value, #9, '\t', True);
  Result := '"' + Value + '"';
end;
procedure OpenShortcut(Path: String);
var Code: Integer;
begin
  if not FileExists(Path) then begin
    MsgBox('Genvejen kunne ikke findes. Åbn Makerværkstedet fra skrivebordet, eller få hjælp af den IT-ansvarlige.', mbError, MB_OK);
    Exit;
  end;
  if not ShellExecAsOriginalUser('open', Path, '', '', SW_SHOWNORMAL, ewNoWait, Code) then
    MsgBox('Makerværkstedet kunne ikke åbnes. Prøv genvejen på skrivebordet.', mbError, MB_OK);
end;
procedure OpenMain(Sender: TObject);
begin
#if AppRole == "server"
  OpenShortcut(ExpandConstant('{commondesktop}\Makerværkstedet.lnk'));
#else
  OpenShortcut(ExpandConstant('{userdesktop}\Makerværkstedet.lnk'));
#endif
end;
procedure OpenLocalLaser(Sender: TObject);
begin
  OpenShortcut(ExpandConstant('{commondesktop}\Elevhub Laserstation.lnk'));
end;
procedure OpenLog(Sender: TObject);
var Code: Integer;
begin
  if not ShellExec('open', ExpandConstant('{sys}\notepad.exe'), '"' + ExpandConstant('{log}') + '"', '', SW_SHOWNORMAL, ewNoWait, Code) then
    MsgBox('Logfilen kunne ikke åbnes. Du kan kopiere filstien fra feltet.', mbError, MB_OK);
end;
procedure OpenConnection(Sender: TObject);
var Code: Integer; Target: String;
begin
  if FileExists(ConnectionExportPath) then Target := '/select,"' + ConnectionExportPath + '"'
  else Target := '"' + ExpandConstant('{commondocs}\Elevvaerkstedet') + '"';
  if not ShellExecAsOriginalUser('open', 'explorer.exe', Target, '', SW_SHOWNORMAL, ewNoWait, Code) then
    MsgBox('Forbindelsesfilens mappe kunne ikke åbnes. Den ligger i Fælles dokumenter under Elevvaerkstedet.', mbError, MB_OK);
end;
function FinishButton(Caption: String; Click: TNotifyEvent): TNewButton;
begin
  Result := TNewButton.Create(WizardForm);
  Result.Parent := WizardForm.FinishedPage; Result.Caption := Caption;
  Result.Left := WizardForm.FinishedLabel.Left;
  Result.Width := ScaleX(220); Result.Height := ScaleY(28);
  Result.OnClick := Click; Result.Visible := False;
end;
function InitializeSetup: Boolean;
var ProgramRoot, DataRoot, Marker: String; HasProgram, HasData: Boolean;
begin
#if AppRole == "server"
  ProgramRoot := ExpandConstant('{commonpf}\ElevhubServer');
  DataRoot := ExpandConstant('{commonappdata}\ElevhubServer');
  Marker := DataRoot + '\installation-owner.json';
#else
  ProgramRoot := ExpandConstant('{localappdata}\MakervaerkstedetLaserstation');
  DataRoot := ProgramRoot;
  Marker := ProgramRoot + '\active.json';
#endif
  HasProgram := DirExists(ProgramRoot) or FileExists(ProgramRoot);
  HasData := DirExists(DataRoot) or FileExists(DataRoot);
  ExistingInstallation := HasProgram and HasData and FileExists(Marker);
  Result := ((not HasProgram) and (not HasData)) or ExistingInstallation;
  if not Result then MsgBox('Der findes en ufuldstændig eller ukendt installation. Mapperne er bevaret. Åbn den eksisterende administration, og få installationen kontrolleret før du fortsætter.', mbError, MB_OK);
end;
procedure InitializeWizard;
begin
  NativeAttempted := False; NativeSucceeded := False;
  WizardForm.ReadyMemo.WordWrap := True; WizardForm.ReadyMemo.ScrollBars := ssVertical;
  if ExistingInstallation then
    InfoPage := CreateOutputMsgPage(wpWelcome, 'Opdatering fundet', 'Din eksisterende opsætning beholdes.',
#if AppRole == "server"
      'Guiden opdaterer hovedsystemet. Skoleprofil, identitet, projekter og indstillinger bevares.' + #13#10#13#10 + 'Køerne sættes på pause under opdateringen og forbliver pauset bagefter. Læreren kontrollerer dem, før arbejdet fortsætter.')
#else
      'Guiden opdaterer denne laserstation. Skolens forbindelse, maskinopsætning og gemte laserplader bevares. Skolens server og køer bliver ikke geninstalleret.')
#endif
  else
    InfoPage := CreateOutputMsgPage(wpWelcome, 'Ny installation', 'Gør Makerværkstedet klar på denne computer.',
#if AppRole == "server"
      'Du vælger computerens rolle, skolens navn og netværk, et farvetema og lasermodel.' + #13#10#13#10 + 'Hav computerens faste IPv4-adresse og elevnettets oplysninger klar. Få dem fra den IT-ansvarlige.' + #13#10#13#10 + 'Efter installationen vælger du skolens lærerkode i lærerpanelet.')
#else
      'Denne computer bliver laserstation og forbindes til skolens eksisterende server.' + #13#10#13#10 + 'Hav filen laser-station-connection.json fra serveren klar. Du skal kun vælge filen; serveradresse og lasermodel følger med.')
#endif
    ;
#if AppRole == "server"
  RolePage := CreateInputOptionPage(InfoPage.ID, 'Computerens rolle', 'Trin 1 af 4 · Vælg din opsætning', 'Vælg den computer, du installerer på. Begge opsætninger kan bruges med elevcomputere.', True, False);
  RolePage.Add('Server · laserstationen er på en anden computer');
  RolePage.Add('Alt på én computer · server og laserstation sammen');
  RolePage.SelectedValueIndex := -1;
  SchoolPage := CreateInputQueryPage(RolePage.ID, 'Skolens opsætning', 'Trin 2 af 4 · Navn og netværk', 'Brug denne computers faste IPv4-adresse eller DHCP-reservation. Elevnettet skrives som en adresse med /maske, fx 192.0.2.0/24. Få hjælp af den IT-ansvarlige, hvis du er i tvivl.');
  SchoolPage.Add('Skolens navn:', False); SchoolPage.Add('Denne computers faste IPv4-adresse:', False);
  SchoolPage.Add('Elevcomputernes netværk (fx 192.0.2.0/24):', False);
  ThemePage := CreateInputOptionPage(SchoolPage.ID, 'Skolens farvetema', 'Trin 3 af 4 · Vælg et udgangspunkt', 'Farven bruges i brugerfladen. Du kan senere vælge skolens egen farve, PNG-logo og skrifttype under Skoleprofil i lærerpanelet.', True, False);
  ThemePage.Add('Rød'); ThemePage.Add('Gul'); ThemePage.Add('Grøn'); ThemePage.SelectedValueIndex := -1;
  MachinePage := CreateInputOptionPage(ThemePage.ID, 'Lasermodel', 'Trin 4 af 4 · Vælg skolens laser', 'Vælg den model, skolens laserstation skal bruge. Installationen starter ingen laser.', True, False);
  MachinePage.Add('Beambox II'); MachinePage.Add('HEXA'); MachinePage.SelectedValueIndex := -1;
#else
  ConnectionPage := CreateInputFilePage(InfoPage.ID, 'Skolens laserforbindelse', 'Trin 1 af 1 · Forbind til skolen', 'Vælg laser-station-connection.json fra skolens server. Den findes normalt i Fælles dokumenter under Elevvaerkstedet. Filen kan have en dato i navnet.');
  ConnectionPage.Add('Skolens forbindelsesfil:', 'JSON-filer|*.json', '.json');
#endif
  OpenButton := FinishButton('Åbn Makerværkstedet', @OpenMain);
#if AppRole == "server"
  OpenButton.Caption := 'Åbn lærerpanel';
#endif
  LaserButton := FinishButton('Åbn laserstation', @OpenLocalLaser);
  ConnectionButton := FinishButton('Vis forbindelsesfil', @OpenConnection);
  LogButton := FinishButton('Vis installationslog', @OpenLog);
  LogPathEdit := TNewEdit.Create(WizardForm); LogPathEdit.Parent := WizardForm.FinishedPage;
  LogPathEdit.Left := WizardForm.FinishedLabel.Left; LogPathEdit.Width := WizardForm.FinishedLabel.Width;
  LogPathEdit.ReadOnly := True; LogPathEdit.Visible := False;
end;
function ShouldSkipPage(PageID: Integer): Boolean;
begin
#if AppRole == "server"
  Result := ExistingInstallation and ((PageID = SchoolPage.ID) or (PageID = RolePage.ID) or (PageID = ThemePage.ID) or (PageID = MachinePage.ID));
#else
  Result := ExistingInstallation and (PageID = ConnectionPage.ID);
#endif
end;
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if ExistingInstallation then Exit;
#if AppRole == "server"
  if CurPageID = SchoolPage.ID then begin
    SchoolPage.Values[0] := Trim(SchoolPage.Values[0]);
    SchoolPage.Values[1] := Trim(SchoolPage.Values[1]);
    SchoolPage.Values[2] := Trim(SchoolPage.Values[2]);
    Result := False;
    if (SchoolPage.Values[0] = '') or (Length(SchoolPage.Values[0]) > 80) then begin
      MsgBox('Skriv skolens navn med 1–80 tegn.', mbError, MB_OK); WizardForm.ActiveControl := SchoolPage.Edits[0]; Exit;
    end;
    if not ValidIPv4(SchoolPage.Values[1], True) then begin
      MsgBox('Skriv denne computers faste IPv4-adresse, fx 192.0.2.20. Brug ikke localhost, 127.0.0.1 eller en webadresse.', mbError, MB_OK); WizardForm.ActiveControl := SchoolPage.Edits[1]; Exit;
    end;
    if not ValidSubnet(SchoolPage.Values[2]) then begin
      MsgBox('Skriv elevnettets adresse og /maske, fx 192.0.2.0/24. Masken skal være mellem /1 og /32. Få oplysningerne fra den IT-ansvarlige.', mbError, MB_OK); WizardForm.ActiveControl := SchoolPage.Edits[2]; Exit;
    end;
    Result := True;
  end;
  if CurPageID = RolePage.ID then Result := RolePage.SelectedValueIndex >= 0;
  if CurPageID = ThemePage.ID then Result := ThemePage.SelectedValueIndex >= 0;
  if CurPageID = MachinePage.ID then Result := MachinePage.SelectedValueIndex >= 0;
#else
  if CurPageID = ConnectionPage.ID then Result := FileExists(ConnectionPage.Values[0]);
#endif
  if not Result then MsgBox('Udfyld oplysningerne, eller vælg en mulighed, før du fortsætter.', mbError, MB_OK);
end;
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := 'Makerværkstedet {#RoleName} · {#ProductVersion}' + NewLine + NewLine;
  if ExistingInstallation then begin
    Result := Result + 'Opdatering af den eksisterende installation.' + NewLine + 'Skoleprofil og gemt arbejde bevares.';
#if AppRole == "server"
    Result := Result + NewLine + 'Køerne forbliver pauset, indtil læreren har kontrolleret dem.';
#endif
    Exit;
  end;
#if AppRole == "server"
  Result := Result + 'Skole: ' + SchoolPage.Values[0] + NewLine;
  if RolePage.SelectedValueIndex = 0 then Result := Result + 'Opsætning: Server med laserstation på en anden computer' + NewLine
  else Result := Result + 'Opsætning: Alt på én computer' + NewLine;
  Result := Result + 'Computerens adresse: ' + SchoolPage.Values[1] + NewLine + 'Elevnet: ' + SchoolPage.Values[2] + NewLine;
  if ThemePage.SelectedValueIndex = 0 then Result := Result + 'Farvetema: Rød' + NewLine;
  if ThemePage.SelectedValueIndex = 1 then Result := Result + 'Farvetema: Gul' + NewLine;
  if ThemePage.SelectedValueIndex = 2 then Result := Result + 'Farvetema: Grøn' + NewLine;
  if MachinePage.SelectedValueIndex = 0 then Result := Result + 'Laser: Beambox II'
  else Result := Result + 'Laser: HEXA';
#else
  Result := Result + 'Forbindelsesfil:' + NewLine + ConnectionPage.Values[0];
#endif
end;
procedure NativeOutput(const S: String; const Error, FirstLine: Boolean);
var Start: Integer; Path, Folder: String;
begin
  Log(S);
  { Keep the actual export, including a possible date suffix. No guessed file. }
  if Pos('laser-station-connection', S) = 0 then Exit;
  Start := Pos(':\', S); if Start < 2 then Exit;
  Path := Trim(Copy(S, Start - 1, Length(S)));
  Folder := ExpandConstant('{commondocs}\Elevvaerkstedet\');
  if (CompareText(Copy(Path, 1, Length(Folder)), Folder) = 0) and FileExists(Path) then ConnectionExportPath := Path;
end;
procedure CurStepChanged(CurStep: TSetupStep);
var Profile, Arguments, Tool: String;
#if AppRole == "server"
var ServerMode, Theme, Machine: String;
#endif
var ExitCode: Integer; ProfileLines: TArrayOfString;
begin
  if CurStep <> ssPostInstall then Exit;
  NativeAttempted := True;
  WizardForm.StatusLabel.Caption := 'Sætter Makerværkstedet op og kontrollerer opstart …';
  WizardForm.FilenameLabel.Caption := 'Det kan tage nogle minutter. Lad guiden blive åben.';
  WizardForm.FilenameLabel.Visible := True;
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
  Profile := '{}';
  if not ExistingInstallation then begin
#if AppRole == "server"
    ServerMode := 'vm'; if RolePage.SelectedValueIndex = 1 then ServerMode := 'makerspace';
    Theme := 'red'; if ThemePage.SelectedValueIndex = 1 then Theme := 'yellow'; if ThemePage.SelectedValueIndex = 2 then Theme := 'green';
    Machine := 'fbb2'; if MachinePage.SelectedValueIndex = 1 then Machine := 'fhexa1';
    Profile := '{"school_name":' + JSONString(SchoolPage.Values[0]) + ',"role_ip":' + JSONString(SchoolPage.Values[1]) + ',"student_subnet":' + JSONString(SchoolPage.Values[2]) + ',"server_mode":' + JSONString(ServerMode) + ',"theme":' + JSONString(Theme) + ',"machine":' + JSONString(Machine);
    { Optional private school colours from the command line only (/BRAND=#rrggbb /BRANDDARK=#rrggbb); lifecycle.py validates them. }
    if (ExpandConstant('{param:BRAND|}') <> '') or (ExpandConstant('{param:BRANDDARK|}') <> '') then
      Profile := Profile + ',"brand":' + JSONString(ExpandConstant('{param:BRAND|}')) + ',"brand_dark":' + JSONString(ExpandConstant('{param:BRANDDARK|}'));
    Profile := Profile + '}';
#else
    Profile := '{"connection_file":' + JSONString(ConnectionPage.Values[0]) + '}';
#endif
  end;
  SetArrayLength(ProfileLines, 1); ProfileLines[0] := Profile;
  if not SaveStringsToUTF8FileWithoutBOM(ExpandConstant('{tmp}\mkv-profile.json'), ProfileLines, False) then RaiseException('Kunne ikke skrive den lokale installationsprofil.');
  Tool := ExpandConstant('{tmp}\mkv-payload\package\runtime\python.exe');
  Arguments := '-B "' + ExpandConstant('{tmp}\mkv-payload\lifecycle.py') + '" --payload "' + ExpandConstant('{tmp}\mkv-payload') + '" --payload-sha256 {#PayloadSha256} --action ' + 'auto' + ' --profile "' + ExpandConstant('{tmp}\mkv-profile.json') + '" --apply';
  if not ExecAndLogOutput(Tool, Arguments, '', SW_SHOWNORMAL, ewWaitUntilTerminated, ExitCode, @NativeOutput) then
    RaiseException('Klargøringen kunne ikke startes. ' + SysErrorMessage(ExitCode));
  if ExitCode <> 0 then RaiseException('Klargøringen afsluttede med fejlkode ' + IntToStr(ExitCode) + '.');
  NativeSucceeded := True;
  except
    FailureReason := GetExceptionMessage; Log('MKV_NATIVE_FAILED: ' + FailureReason);
  end;
  WizardForm.ProgressGauge.Style := npbstNormal;
  if NativeSucceeded then WizardForm.ProgressGauge.Position := WizardForm.ProgressGauge.Max
  else WizardForm.ProgressGauge.State := npbsError;
end;
function GetCustomSetupExitCode: Integer;
begin
  Result := 0;
  if NativeAttempted and not NativeSucceeded then Result := 20;
end;
procedure CurPageChanged(CurPageID: Integer);
var Top: Integer;
begin
  if CurPageID = wpInstalling then WizardForm.FilenameLabel.Visible := False;
  if CurPageID <> wpFinished then Exit;
  if not NativeSucceeded then begin
    WizardForm.FinishedHeadingLabel.Caption := 'Installationen blev ikke færdig';
    WizardForm.FinishedLabel.Caption := FailureReason + #13#10#13#10 + 'Makerværkstedet er ikke meldt klar. Få den IT-ansvarlige til at kontrollere loggen, før installationen genkøres.' + #13#10#13#10 + 'Installationslog (filstien kan kopieres):';
    WizardForm.NextButton.Caption := '&Luk guiden';
    WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
    LogPathEdit.Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(8);
    LogPathEdit.Text := ExpandConstant('{log}'); LogPathEdit.Visible := True;
    LogButton.Top := LogPathEdit.Top + LogPathEdit.Height + ScaleY(8); LogButton.Visible := True;
    Exit;
  end;
  WizardForm.FinishedHeadingLabel.Caption := 'Makerværkstedet er klar';
#if AppRole == "server"
  if ExistingInstallation then
    WizardForm.FinishedLabel.Caption := 'Hovedsystemet er opdateret, og opstarten er kontrolleret.' + #13#10#13#10 + 'Åbn lærerpanelet og kontroller de pausede køer, før arbejdet fortsætter.'
  else if RolePage.SelectedValueIndex = 0 then
    WizardForm.FinishedLabel.Caption := 'Serveren er installeret og startet.' + #13#10#13#10 + 'Åbn lærerpanelet og vælg skolens lærerkode.' + #13#10#13#10 + 'På lasercomputeren: kør laserstationens EXE og vælg den eksporterede laserforbindelse. Brug knappen herunder til at finde filen.'
  else
    WizardForm.FinishedLabel.Caption := 'Hovedsystemet er installeret og startet på denne computer.' + #13#10#13#10 + 'Åbn lærerpanelet og vælg skolens lærerkode. Åbn derefter laserstationen på denne computer.' + #13#10#13#10 + 'Begge genveje findes også på skrivebordet.';
#else
  WizardForm.FinishedLabel.Caption := 'Laserstationen er installeret med skolens forbindelsesfil.' + #13#10#13#10 + 'Åbn Makerværkstedet og log ind med skolens lærerkode. Genvejen findes også på skrivebordet.';
#endif
  WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
  Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(16);
  OpenButton.Top := Top; OpenButton.Visible := True;
#if AppRole == "server"
  if FileExists(ExpandConstant('{commondesktop}\Elevhub Laserstation.lnk')) and
      FileExists(ExpandConstant('{commonappdata}\ElevhubServer\laserstation\laser-installation.json')) then begin
    LaserButton.Top := Top + OpenButton.Height + ScaleY(8); LaserButton.Visible := True;
  end else if not ExistingInstallation and (RolePage.SelectedValueIndex = 0) then begin
    ConnectionButton.Top := Top + OpenButton.Height + ScaleY(8); ConnectionButton.Visible := True;
    if not FileExists(ConnectionExportPath) then ConnectionButton.Caption := 'Åbn forbindelsesmappe';
  end;
#endif
end;

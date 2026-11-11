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
[Languages]
Name: "danish"; MessagesFile: "compiler:Languages\Danish.isl"
[Files]
Source: "{#PayloadRoot}\*"; DestDir: "{tmp}\mkv-payload"; Flags: recursesubdirs createallsubdirs deleteafterinstall
[Code]
var ExistingInstallation: Boolean;
var InfoPage: TOutputMsgWizardPage;
#if AppRole == "server"
var SchoolPage: TInputQueryWizardPage;
var RolePage, ThemePage, MachinePage: TInputOptionWizardPage;
#else
var ConnectionPage: TInputFileWizardPage;
#endif
function JSONString(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  StringChangeEx(Value, #13, '\r', True);
  StringChangeEx(Value, #10, '\n', True);
  StringChangeEx(Value, #9, '\t', True);
  Result := '"' + Value + '"';
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
  if ExistingInstallation then
    InfoPage := CreateOutputMsgPage(wpWelcome, 'Opdatering fundet', '', 'Den eksisterende installation opdateres automatisk. Skoleprofil, identitet og data bevares. Serverens køer forbliver pauset efter opdateringen.')
  else
    InfoPage := CreateOutputMsgPage(wpWelcome, 'Ny installation', '', 'Guiden opretter en ny installation på denne computer. Vælg skolens understøttede opsætning på de næste sider.');
#if AppRole == "server"
  SchoolPage := CreateInputQueryPage(InfoPage.ID, 'Skolens opsætning', '', 'Skriv skolens navn og de netværksoplysninger, du har fået af skolens IT-ansvarlige.');
  SchoolPage.Add('Skolens navn:', False); SchoolPage.Add('Serverens IPv4-adresse:', False);
  SchoolPage.Add('Elevsubnet (CIDR, f.eks. 192.168.10.0/24):', False);
  RolePage := CreateInputOptionPage(SchoolPage.ID, 'Computerens rolle', '', 'Vælg den eksisterende opsætning, der passer til denne computer.', True, False);
  RolePage.Add('Server med separat laserstation (VM/server)');
  RolePage.Add('Fælles makerspace-computer med lokal laserstation');
  RolePage.SelectedValueIndex := -1;
  ThemePage := CreateInputOptionPage(RolePage.ID, 'Skolens farvetema', '', 'Vælg skolens tema.', True, False);
  ThemePage.Add('Rød'); ThemePage.Add('Gul'); ThemePage.Add('Grøn'); ThemePage.SelectedValueIndex := -1;
  MachinePage := CreateInputOptionPage(ThemePage.ID, 'Lasermodel', '', 'Vælg skolens understøttede lasermodel. Den separate laserstation bruger også sin egen forbindelsesfil.', True, False);
  MachinePage.Add('Beambox II'); MachinePage.Add('HEXA'); MachinePage.SelectedValueIndex := -1;
#else
  ConnectionPage := CreateInputFilePage(InfoPage.ID, 'Skolens laserforbindelse', '', 'Vælg forbindelsesfilen fra skolens server. Filen indeholder skolens eksisterende stationstilknytning.');
  ConnectionPage.Add('Forbindelsesfil:', 'JSON-filer|*.json', '.json');
#endif
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
  if CurPageID = SchoolPage.ID then Result := (Trim(SchoolPage.Values[0]) <> '') and (Trim(SchoolPage.Values[1]) <> '') and (Trim(SchoolPage.Values[2]) <> '');
  if CurPageID = RolePage.ID then Result := RolePage.SelectedValueIndex >= 0;
  if CurPageID = ThemePage.ID then Result := ThemePage.SelectedValueIndex >= 0;
  if CurPageID = MachinePage.ID then Result := MachinePage.SelectedValueIndex >= 0;
#else
  if CurPageID = ConnectionPage.ID then Result := FileExists(ConnectionPage.Values[0]);
#endif
  if not Result then MsgBox('Udfyld oplysningerne, eller vælg en mulighed, før du fortsætter.', mbError, MB_OK);
end;
procedure CurStepChanged(CurStep: TSetupStep);
var Profile, Arguments, Tool: String;
#if AppRole == "server"
var ServerMode, Theme, Machine: String;
#endif
var ExitCode: Integer; ProfileLines: TArrayOfString;
begin
  if CurStep <> ssPostInstall then Exit;
  Profile := '{}';
  if not ExistingInstallation then begin
#if AppRole == "server"
    ServerMode := 'vm'; if RolePage.SelectedValueIndex = 1 then ServerMode := 'makerspace';
    Theme := 'red'; if ThemePage.SelectedValueIndex = 1 then Theme := 'yellow'; if ThemePage.SelectedValueIndex = 2 then Theme := 'green';
    Machine := 'fbb2'; if MachinePage.SelectedValueIndex = 1 then Machine := 'fhexa1';
    Profile := '{"school_name":' + JSONString(SchoolPage.Values[0]) + ',"role_ip":' + JSONString(SchoolPage.Values[1]) + ',"student_subnet":' + JSONString(SchoolPage.Values[2]) + ',"server_mode":' + JSONString(ServerMode) + ',"theme":' + JSONString(Theme) + ',"machine":' + JSONString(Machine) + '}';
#else
    Profile := '{"connection_file":' + JSONString(ConnectionPage.Values[0]) + '}';
#endif
  end;
  SetArrayLength(ProfileLines, 1); ProfileLines[0] := Profile;
  if not SaveStringsToUTF8FileWithoutBOM(ExpandConstant('{tmp}\mkv-profile.json'), ProfileLines, False) then RaiseException('Kunne ikke skrive den lokale installationsprofil.');
  Tool := ExpandConstant('{tmp}\mkv-payload\package\runtime\python.exe');
  Arguments := '-B "' + ExpandConstant('{tmp}\mkv-payload\lifecycle.py') + '" --payload "' + ExpandConstant('{tmp}\mkv-payload') + '" --payload-sha256 {#PayloadSha256} --action ' + 'auto' + ' --profile "' + ExpandConstant('{tmp}\mkv-profile.json') + '" --apply';
  if not Exec(Tool, Arguments, '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then RaiseException('Installationsruntime kunne ikke startes.');
  if ExitCode <> 0 then RaiseException('Installationen/opdateringen blev afbrudt. Programbackup, manuel databasebackup og køpause bevares, hvis de er oprettet; se installationsloggen. Ukendt installation ændres ikke.');
end;

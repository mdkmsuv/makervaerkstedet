# Kodeagentens vejledning til skoletilpasning

**Kreditering: Klostermarksskolen.** Dette dokument beskriver, hvordan en
kodeagent tilpasser kildekoden til en skole. Makerværkstedet er lærerens
hovedsystem; Linux-elevcomputerne og Elevhub er delkomponenter. En tilpasning må
derfor bevare lærerens kontrolpanel, datakontrakter og eksisterende
sikkerhedskontroller. Dokumentet ændrer ikke en installation, starter ikke en
tjeneste og bygger ikke en pakke.

Kun Linux bruges til elevværkstederne. Windows bruges til serveren og
lærerens browseradgang; den tidligere Windows-elevklient er en lukket vej.

## Før arbejdet

Læs først [Makerværkstedets arkitektur](MAKERSPACE-ARKITEKTUR.md) og
projektets aktuelle kode og dokumentation. Denne vejledning er kun en indgang for en
kodeagent, der ændrer kildekode og tests; den er ikke en vejledning til en
AI-agent, der skal styre skolens daglige drift.
Afklar derefter, om opgaven gælder:

- en skoleprofil eller en eksisterende installation,
- en Windows-server med separat LAN,
- Linux-Elevhub som klient,
- en maskine, et workflow eller kun lærerens overblik.

Brug midlertidige testdata og en ny testmappe. Kopier ikke skolens
adgangsnøgler, elevdata eller private konfiguration ind i eksempler.
Brug relative kildehenvisninger og syntetiske værdier i tests. En opgave om
en konkret installation kan kræve læsning af dens konfiguration; hold den
adskilt fra generisk kildekode og dokumentation.

## Skoleprofilen

Windows-skolens `school.json` oprettes af
[Setup-School.ps1](../deployment/school-setup/Setup-School.ps1), ikke af
Makerspace-databasen. Scriptets profil har `schemaVersion: 1` og felterne
`school_name`, `theme`, `role`, `server`, `student_subnet` og
`admin_public_key`. `role` er `vm` eller `makerspace`; `theme` er en af de
godkendte farver. `enrollment.py` validerer profilen i
[`deployment/school-setup/enrollment.py`](../deployment/school-setup/enrollment.py)
og kræver blandt andet schema, serveradresse og et gyldigt IPv4-net.

`Setup-School.ps1` bruger `-SchoolName`, `-Role`, `-RoleIp`,
`-StudentSubnet` og `-Theme`. Ved eksisterende installation sammenligner det
profilen med ejerskabsmarkøren og afviser en uventet skole, rolle, server eller
subnet. En kodeagent skal ændre valideringen og skabelonen samlet, hvis
profilformatet ændres; den må ikke skrive en ny privat profil ud fra gæt.

## Offentlig standard og skolens lokale udseende

Den offentlige Makerværkstedet-udgave skal bruge projektets eksisterende
Bauhaus-inspirerede skins som standard. Den lokale Klostermarksskolen-profil er
installationsdata, som skolen vælger og vedligeholder lokalt. Den skal ikke
gøres til standardtema eller kopieres ind i den offentlige kilde- eller
udgivelsesbygning. Den offentlige udgave bevarer krediteringen af
Klostermarksskolen og ophavet til Makerværkstedet, Mathias Kusk Damgaard.

En installeret Windows-komponent kan have mappen `local-appearance` i hver af
de tre dataområder: Elevhub, det centrale designværksted og Printvaerksted.
Mappen indeholder et `manifest.json` og de lokale filer, som manifestet
beskriver. En neutral manifeststruktur kan for eksempel se sådan ud:

```json
{
  "schemaVersion": 1,
  "school_name": "Eksempelskolen",
  "theme": "green",
  "style": {
    "website_css": "website.css",
    "website_css_sha256": "<sha256>",
    "logo": "logo.svg",
    "logo_sha256": "<sha256>"
  }
}
```

`app/school_appearance.py` læser kun almindelige lokale filer, afviser
reparse points og ugyldige relative stier, begrænser filstørrelser og
kontrollerer SHA-256-værdien fra manifestet. `web/school-theme.js` henter den
valgte skoleidentitet fra appearance-endpointet. Et lokalt stylesheet og logo
bliver derefter serveret fra den validerede mappe. Findes ingen lokal
appearance-mappe, kan skoleprofilens navn og farvetema bruges. Findes mappen
med et manglende eller beskadiget manifest, afviser fladen med HTTP 503;
den skifter ikke automatisk til udgivelsens standardudseende.

En eksisterende lokal appearance skal behandles som driftsdata. Den må ikke
overskrives af release-skindata, når programkoden opdateres. Den eksisterende
Windows-opdaterer tager derfor en separat bevaringskopi af eksisterende `school.json`
og alle filer under de tre `local-appearance`-mapper. Den gemmer en kvittering
med tidspunkt, format og hash for hver fil, kontrollerer at dataene er
uændrede før og efter kandidatens migration og kan gendanne den gemte
appearance ved rollback. En mappe uden `manifest.json` kan ikke indgå i den
lokale appearance-kopi, og et beskadiget manifest bliver ikke brugt som gyldigt
udseende af runtime-læseren. En opdatering gætter sig derfor ikke til skolens
udseende.

Læg ikke lokale logoer, stylesheets, skoleprofiler eller installationens andre
appearance-filer i GitHub-udgivelsens generiske eksempeldata. Tilpasningen skal
kun bruge neutrale eksempelnavne og pladsholdere i kildekode og tests. Den
beskrevne kildekontrol dokumenterer arkitekturen og bevaringsflowet; den er
ikke i sig selv bevis på, at en bestemt skoles tema er aktiveret på live-udstyr.

Linux bruger ikke automatisk Windows-profilen. På Windows læser
[`app/school_appearance.py`](../app/school_appearance.py) `school.json` fra
datamappens forælder. På Linux læses den lokale kioskprofil fra
`/etc/elevhub/kiosk.json`. Den fælles betydning er skolens navn og tema, mens
serverrolle, elevnet og enrollment hører til serverprofilen.

## Separat Windows-LAN og serverstart

Den Windows-server, der står på skolens LAN, konfigureres gennem
[`windows-elevhub/server/install.ps1`](../windows-elevhub/server/install.ps1).
De relevante valg er server-IP, elev-subnet, læreradresse og -port samt
`PrintBackendPort`, `DesignBackendPort`, `TeacherBackendPort` og eventuelt
`UseExistingPrintBackend`. Scriptet validerer aktiv IPv4-adresse, subnet,
porte, firewall og ejerskab. Det skal køres med de faktiske skoleværdier af
en administrator. Under den aktuelle modning arbejder kodeagenten i kildekode
og isolerede tests; faktisk installation kræver en opgave om den installation.

Den genererede runtime-konfiguration kontrolleres af
[`windows-elevhub/server/bootstrap.py`](../windows-elevhub/server/bootstrap.py).
Den kræver lokale TLS-filer og register under `security`, validerer
`konfiguration.json`, og starter de autoritative lokale tjenester med
datamapperne `printvaerksted`, `designvaerksted` og `hub`. Et separat LAN
kræver derfor, at adresser og firewallregler stemmer overens i skoleprofilen,
Windows-installationen og serverens konfiguration.

[`windows-elevhub/server/hub/konfiguration.windows.json`](../windows-elevhub/server/hub/konfiguration.windows.json)
er kilde-skabelonen. Den beskriver blandt andet `backend_ports` og apps med
`port`, `bagved`, `regler` og standardåben. En skolekonfiguration skal holde
værkstedsappens backend på loopback og lade serverens LAN-adresse være den
beskyttede lærer-/Elevhub-adresse. Brug
[`windows-elevhub/server/provision.py`](../windows-elevhub/server/provision.py)
til enrollmentdata; kopier ikke enrollmentnøgler ind i en generisk pakke.

## Linux-apps og Windows-apps skal være enige

Linux-kildens tilsvarende appskabelon er
[`linux-elevhub/server/elevhub/konfiguration.json`](../linux-elevhub/server/elevhub/konfiguration.json).
Sammenlign app-id, type, åbning som standard, elevport, backend, regler og
visningsnavn med Windows-skabelonen. Tilføj ikke en app i kun den ene platform:
Elevhub-klienten kan ellers vise en indgang, som serveren ikke kan levere, eller
serveren kan åbne en backend, som klienten ikke kender.

`deployment/school-setup/Setup-School.ps1` er skoleopsætning; den er ikke en
makerspace-appgenerator. Linux' kiosk- og appindgange ligger i
[`linux-elevhub/ansible/roles/kiosk`](../linux-elevhub/ansible/roles/kiosk),
mens lærerens værkstedsstatus går gennem Elevhub-serverens eksisterende
politik. Ved ændringer skal den relevante platformtest udvides, og den anden
platforms schema kontrolleres.

## Maskiner og workflows

Maskiner og workflows skal oprettes gennem lærerens eksisterende
Makerværksted-panel og dets domænefunktioner. Brug
[`makerspace_store.py`](../linux-elevhub/server/elevhub/makerspace_store.py) og
[`makerspace_workflows.py`](../linux-elevhub/server/elevhub/makerspace_workflows.py)
som kildekodekontrakt sammen med panellets eksisterende kald. En
skoletilpasning skal ikke indsætte maskiner eller workflows direkte i SQLite,
udvide systemet med en ny automatisk driftsklient eller opfinde en parallel kø.

En ny maskine kan registreres uden at være understøttet hardware. En driver,
forbindelse, modelgrænse, fysisk status og adgang til start/frigivelse skal
fortsat håndhæves af den eksisterende adapter. For Bambu-printere går
administrationen gennem [`makerspace_printers.py`](../linux-elevhub/server/elevhub/makerspace_printers.py)
til den eksisterende lokale printertjeneste. For laserstationer er
[`makerspace_laser_admin.py`](../linux-elevhub/server/elevhub/makerspace_laser_admin.py)
og den autoritative Inbox-kø relevante; registrering eller workflowbinding er
ikke bevis på, at en fysisk laser er online.

Workflows oprettes, valideres, publiceres og tilbageføres i lærerens panel.
Et aktivt job fastholder den publicerede version. En tilpasning må derfor ikke
ændre workflowdefinitioner direkte i en jobrække, slå lærertrin fra eller
fjerne `approve`, `produce`, fysisk kontrol og `clear` fra maskinadapterens
kontroller. Se [arkitekturens workflowafsnit](MAKERSPACE-ARKITEKTUR.md#workflows-der-kan-tilpasses)
for grænsen mellem redigerbare lærerflows og faste sikkerhedsbetingelser.

## Privat konfiguration af projektbibliotekets adresse

Når det lokale projektbibliotek bruger en anden port end 8791, lægges
`print-project-origins.json` i printkomponentens datamappe og
`laser-project-origins.json` i den centrale designkomponents datamappe.
Find de aktuelle mapper gennem installationens komponentvalg eller
tjenesternes `--data`; redigér ikke en låst programgeneration.
Konfigurationen følger komponentens backup og gendannelse.

```json
{"allowed_origins":["http://127.0.0.1:8793","http://localhost:8793"]}
```

Listen erstatter standardlisten. Den må kun indeholde præcise HTTP-loopback-
origins med port; en tom liste slår indlejring fra. Tjenesten afviser ugyldig
konfiguration før binding af HTTP-porten. Lærerflader tillader altid ingen
indlejring. Se [projektbroens kontrakt](MAKERSPACE-ARKITEKTUR.md#print--og-designtjenestens-lokale-projektbro)
for validering og forskellen mellem produktionsindgang og kildeintegration.

## Hvad der er kilde, drift og fremtidig bygning

Filer under `linux-elevhub/`, `windows-elevhub/`, `deployment/`, `app/` og
`docs/` er udviklingskilde. `data/`, lokale skoleprofiler, TLS, enrollment,
bridge-nøgler og SQLite-databaser er driftsdata og må ikke bruges som
skabeloner. Mapper under `output/` og `dist/` er historiske eller verificerede
udgivelser; de skal ikke ændres som led i denne tilpasning.

En ændring er først driftsegnet, når relevante tests og kildekontroller er
bestået. Det er fortsat en udviklingsændring, indtil en senere samlet bygning
er godkendt. Byg ikke en ISO, Windows-pakke eller downloaddel efter en enkelt
rettelse. Dokumentér i stedet hvilke tests der kørte, hvilke skoledata der
ikke blev berørt, og hvilke LAN-, hardware- eller elevudstyrsprøver der stadig
kræver skolens udstyr.

## Minimumskontrol for en kodeagent

1. Find den autoritative generator eller skabelon; gæt ikke en skoleprofil.
2. Kontrollér schema, app-id’er, porte, backend og datamapper på begge platforme.
3. Genbrug lærerens panel, versioner, audit og eksisterende adaptere.
4. Hold credentials, elevdata, private stier og skoleværdier ude af kildekode,
   tests og dokumentation.
5. Kør isolerede relevante tests med midlertidige datamapper, lokale
   testtjenester og simuleret hardware; forbind ikke til skolens drift.
6. Meld eksplicit, hvis ændringen kræver fysisk pilot, ny samlet bygning eller
   en fremtidig adapter.


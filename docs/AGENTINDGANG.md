# Agentindgang: tilpas Makerværkstedet til en skole

**Kreditering: Klostermarksskolen.**

Denne indgang er til en **kodeagent**, der hjælper en skole med at forstå,
konfigurere og ændre Makerværkstedet. Den består af Markdown-filer med
arkitektur, filhenvisninger, tilpasningspunkter og kontrolkrav. Læreren styrer
maskiner, workflows og den daglige drift gennem kontrolpanelet.

Elevværkstederne målrettes kun Linux. Windows-serveren og lærerens
browseradgang er understøttet; den historiske Windows-elevklient skal ikke
genoplives som en del af skoletilpasningen.

Der er ingen særskilt agentlogin, agentnøgler, agentpanel eller agent-CLI.
En kodeagent arbejder i de filer og det miljø, skolens ansvarlige giver den
adgang til. Den får ikke en skjult driftsidentitet i programmet. En brugers
konkrete instruktioner afgør opgaven og hvilke ændringer, der er ønsket.

## Læs i denne rækkefølge

| Dokument | Hvad det afklarer |
|---|---|
| [Projektindgang](../AGENTS.md) | Installation, roller og grundlæggende krav ved ændringer. |
| [Arkitektur](MAKERSPACE-ARKITEKTUR.md) | Komponenter, dataejerskab, maskiner og workflowgrænser. |
| [Skoletilpasning](SKOLETILPASNING.md) | Skoleprofil, LAN, værksteder og de faktiske generatorer og skabeloner. |
| Kildekode og testkørsel | Kontroller den aktuelle kode og relevante testkommandoer i projektet. |
| [Serverkontrolpanel](SERVERKONTROLPANEL.md) | Lærerens samlede overblik. |
| [Backup](MAKERSPACE-BACKUP.md) og [opdatering](MAKERSPACE-OPDATERING.md) | Databevarelse, isolerede prøver og grænser for ibrugtagning. |
| [Kreditering](../CREDITS.md) og [offentlig udgivelse](PUBLIC-RELEASE.md) | Obligatorisk kreditering, tredjepartslicenser og mangler før deling. |

Makerværkstedet er hovedsystemet. Linux-elevcomputere er en delkomponent;
maskinadministration og workflows skal fungere med nul elevcomputere.

## Find det rigtige sted at ændre

| Skolens ønske | Start her |
|---|---|
| Navn, tema, rolle eller LAN-opsætning | [Skoletilpasning](SKOLETILPASNING.md) og [Setup-School.ps1](../deployment/school-setup/Setup-School.ps1). |
| Tilføje en understøttet printer eller en manuel station | Lærerens `/makerspace`-panel. Koden ligger i [makerspace_store.py](../linux-elevhub/server/elevhub/makerspace_store.py) og [makerspace_printers.py](../linux-elevhub/server/elevhub/makerspace_printers.py). |
| Ændre en arbejdsgang | Lærerens workfloweditor og [makerspace_workflows.py](../linux-elevhub/server/elevhub/makerspace_workflows.py). |
| Tilføje en ny maskintype | Undersøg den eksisterende adapter og den autoritative kø først. En manuel registrering giver ikke automatisk en hardwaredriver. |
| Ændre digitale værksteder | Appskabelonerne i [skoletilpasningen](SKOLETILPASNING.md), [apps.py](../linux-elevhub/server/elevhub/apps.py) og [tilstand.py](../linux-elevhub/server/elevhub/tilstand.py). |
| Ændre lærerens overblik | [system_overview.py](../linux-elevhub/server/elevhub/system_overview.py) og [web-mappen](../linux-elevhub/server/elevhub/web). |
| Ændre printerforløbet | [core.py](../app/core.py), [printer_bridge.py](../app/printer_bridge.py) og [printerfrigivelse](PRINTERFRIGIVELSE.md). |
| Ændre laserforløbet | [laser_server.py](../app/laser_server.py), [laser_station.py](../app/laser_station.py) og [laseradapteren](../linux-elevhub/server/elevhub/makerspace_laser_admin.py). |
| Ændre backup eller versionsskift | [installation_backup.py](../app/installation_backup.py), [generation_transaction.py](../app/generation_transaction.py), [generation_activation.py](../app/generation_activation.py) og [generation_rollback.py](../app/generation_rollback.py). Læs først [opdateringsstatus](MAKERSPACE-OPDATERING.md); tilbageførsel skal bevare aktuelle data. |

Foretræk en eksisterende indstilling eller lærerfunktion, når den kan løse
opgaven. Ved en kodeændring skal generator, validering og berørte klienter
følge med. Skriv ikke direkte i kø- eller workflowdatabaser for at omgå
programmets funktioner. Det kan ændre igangværende jobs eller miste historik.

## Arbejdsforløb for en skoletilpasning

1. Beskriv skolens ønskede ændring, relevante maskiner og netværksforhold.
   Find den autoritative kilde i tabellen ovenfor. Brug syntetiske testdata;
   kopier ikke private skoleprofiler, elevfiler eller nøgler til eksempler.
2. Gennemfør ændringen i konfiguration eller kildekode. Bevar lærerens kontrol,
   aktive jobs' workflowversioner og de eksisterende maskinkontroller.
3. Kør relevante isolerede tests. Brug midlertidige datamapper og simuleret
   hardware; loopback-HTTP/TLS kan bruges til integrationstest.
4. Opdatér den relevante vejledning med det faktiske tilpasningspunkt.
   Beskriv resultat, verifikation og resterende afprøvning på skolens udstyr.
5. Hold udviklingsændringer adskilt fra installation. Vælg den dokumenterede
   installations- eller opdateringsvej for målcomputerens rolle, og kontrollér
   backup, installeret version og databevarelse ved praktisk afprøvning.
   En filkontrol alene dokumenterer ikke en vellykket installation.

## Kontroller efter ændringer

Kør fra projektroden med projektets Python-miljø. Vælg de prøver, som matcher
ændringen; et bestået delområde beviser ikke, at hele systemet er driftsklart.

~~~powershell
# Lærerens administration, adgang og workflows
.venv\Scripts\python.exe -m unittest discover -s linux-elevhub/server/elevhub -p 'test_makerspace*.py' -q

# Printer/laser mellem lærerpanel og de lokale tjenester
.venv\Scripts\python.exe -m unittest tests.test_makerspace_printer_integration tests.test_makerspace_laser_integration tests.test_makerspace_queue_control -q

# Offlinepolitik på elevklienten
.venv\Scripts\python.exe -m unittest discover -s linux-elevhub/tests -p test_internetregler.py -q
node linux-elevhub/tests/test_kiosk_offline_reconnect.cjs

# Windows-bootstrap i isolerede softwaretests
.venv\Scripts\python.exe -m unittest discover -s windows-elevhub/server/tests -p 'test_*.py' -q
~~~

Ved ændringer i dataformater, installation eller opdatering skal de relevante
backup-, migrations- og opdateringsprøver fra [opdateringsvejledningen](MAKERSPACE-OPDATERING.md)
også køres. Windows-tjenester, fysisk hardware og den enkelte Chromebook-model
kræver særskilt praktisk afprøvning.

## Forhold som skal bevares

- Læreren styrer systemet. De interne HTTP-funktioner under `/api/makerspace/`
  betjener lærerens panel og kræver lærerlogin. Private forbindelsesnøgler
  mellem lokale tjenester er komponentkommunikation, ikke agentadgang.
- Ved serverudfald er alle elevværksteder, CaDoodle og Internet åbne, også ved
  kold opstart. Ved genforbindelse gælder lærerens aktuelle valg igen.
- Printerkø og laser-Inbox ejer deres egne job. Et workflow eller en ny
  tilpasning må ikke omgå fysisk kontrol, lærerbekræftelse eller pladerydning.
- Nye understøttede maskiner skal kunne opsættes gennem administrationen;
  helt nye protokoller kræver integration og afprøvning.
- Skolens navn og tema kan ændres. **Kreditering: Klostermarksskolen.** skal
  fortsat fremgå af program, dokumentation og udgivelser. Tredjepartslicenser
  bevares; [licensafgrænsningen](../LICENSE-SCOPE.md) beskriver projektets egne
  GPL- og MIT-komponenter samt særskilte tredjepartsnotices.

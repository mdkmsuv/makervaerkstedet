# Makerværkstedet: samlet styring for læreren

Arkitekturen er implementeret i
udviklingskoden; tabellen skelner mellem den og de resterende integrationer.
Modningsarbejdet foregår i kode og tests; nye ISO- og Windows-pakker er på hold.

Makerværkstedet er hovedsystemet. Læreren administrerer maskiner og
arbejdsgange; elevcomputere, herunder Linux-enheder via Elevhub, er klienter
og delkomponenter. Det fælles register og workflows må ikke
kræve, at der er tilmeldt eller tilsluttet en elevcomputer.

Brugerens præcisering: **agentindgang er Markdown til en kodeagent, der
tilpasser programmet til en skole**. Den særskilte daglige agentstyring er
fjernet fra udviklingskoden. Lærerlogin er den eneste adgang til
Makerværkstedets kontrolpanel og dets HTTP-funktioner. Se
[kodeagentens startvejledning](AGENTINDGANG.md) og
[skoletilpasning](SKOLETILPASNING.md).

## Hvad der findes, og hvad der mangler

| Område | Aktuel kode | Næste skridt |
|---|---|---|
| 3D-printere | `makerspace_printers.py` forbinder kontrolpanelet med den eksisterende printertjeneste. Printertjenesten ejer opsætning, versionskontrol og genforsøgskvitteringer; `workflow_jobs` returnerer højst 100 poster. | Fysisk pilot og fortsat afprøvning af workflowgaten. |
| Laser | Stationer registreres med lokal station-id/model, bindes til publicerede laserworkflows, og eksisterende Inbox-job kan bindes og føres gennem lærerens klargørings-/handoffgate. | Controller-bootstrap, fysisk hardwaretest og fjernstationer. |
| Manuelle stationer | `makerspace_store.py` registrerer/redigerer navn, placering, aktivering og workflow; ens navn/placering afvises. | Fælles adapteradministration til understøttet hardware. |
| Workflows | `makerspace_workflows.py` og `makerspace_store.py` giver kladde, validering, publicering, tilbageførsel og fastlåst version for manuelle opgaver; printerjob har nu en separat gate i printkøen. | Afprøv den nye laserbinding mod den autoritative lasertjeneste; fysisk status og fjernstationer er fortsat udenfor kontrakten. |
| Elevcomputere og værksteder | Elevhub har tilmelding, lærerpolitik, status og versioner. | Bevar enhedsrollerne og saml status med maskiner og køer. |
| Kontrolpanel | `system_overview.py` samler printer- og elevstatus; `/makerspace` administrerer printere, laserstationer, laserworkflows og jobs bag lærerlogin. | Controller-bootstrap, fysisk hardwaretest og vedligeholdelse. |
| Agentindgang | `AGENTS.md`, `docs/AGENTINDGANG.md` og `docs/SKOLETILPASNING.md` beskriver arkitektur og ændringspunkter for en kodeagent. Ingen driftsagent, agentnøgler eller agent-CLI. | Hold vejledninger og filhenvisninger i trit med koden. |
| Køstyring | Læreren kan pause/genoptage printkøen med `printer.queue.pause`. Versionskontrol og atomiske kvitteringer beskytter mod genafspilning og gamle ændringer. | Afprøv forløbet i skolepiloten. |

De nye moduler ligger i `linux-elevhub/server/elevhub/`, fordi lærerens
eksisterende HTTPS-tjeneste er værten. De kræver ingen elevregistrering.
Printere observeres via `PrinterSnapshot`; ændringer går til den samme
eksisterende printertjeneste gennem en særskilt lokal nøgle. FLUX-stationer
observeres via `/api/station/overview`; der er endnu ingen fysisk laserprobe.
En loginstatus eller et installeret laserværktøj tæller ikke som bevis på, at
den fysiske laser er online eller klar. [Kodeagentens vejledning](AGENTINDGANG.md)
viser vej til de relevante komponenter og begrænsninger.

`inventory` giver stabile ID'er med præfikserne `manual:`, `bambu:` og `flux:`.
`source_id` henviser til den autoritative komponents eget ID. Printerændringer
bruger printerlistens `id` og `version`; kopier af opsætningen oprettes ikke i
det manuelle stationsregister. Gamle klienters `machines`-felt indeholder
fortsat kun manuelle stationer.

## Maskiner og forbindelser

Et fælles register skal beskrive stabilt ID, navn, type, placering, valgt
adapter, understøttede funktioner, aktivering og workflowtilknytning.
Status skal angive kilde, tidspunkt og kendt/ukendt/offline. Hemmeligheder
gemmes som beskyttede forbindelsesoplysninger og må ikke returneres i et
overblik eller dokumentation.

Tilføjelse i administrationen skal føre gennem valg af maskintype/model,
navn/placering, forbindelse, afprøvning og valg af workflow. Nye maskiner
starter uden automatisk produktion. Manuel registrering skal være mulig for
udstyr uden driver; det giver overblik og opgaver, ikke fjernstyring.

Begynd med adaptere til eksisterende Bambu- og FLUX-funktioner. De nuværende
køer, tilstande og datalagre er fortsat autoritative. En ny fælles facade må
ikke oprette en konkurrerende kø eller en kopi af maskinens aktive opsætning.
En ny hardwareprotokol kræver en adapter og test; at indtaste en model i
registret er ikke i sig selv driverunderstøttelse.

## Workflows, der kan tilpasses

Et workflow er en versioneret skabelon med trin, roller, tilladte handlinger
og betingelser for at fortsætte. Eksempler er indsendelse, forberedelse,
lærergodkendelse, produktion, afhentning og manuel kontrol. Behovene er
forskellige for print, laser og en manuel station.

Første editor skal kunne kopiere en skabelon, ændre trin/valg, validere en
kladde, vise konsekvenser og aktivere en ny version. Et aktivt job skal
fastholde sin workflowversion. Tilbageførsel ændrer fremtidige job; den må
ikke omskrive historik eller flytte igangværende maskiner til en anden tilstand.

Skabeloner skal ikke indeholde vilkårlig kode eller shellkommandoer.
Maskinadapteren håndhæver fortsat eksempelvis modelgrænser, identitet,
forbindelse og adgang til at starte eller frigive. Redigering af en
arbejdsgang må ikke kunne slå disse kontroller fra.

## Workflowgate for print

En printerbinding oprettes kun gennem den eksplicitte handling
`printer.workflow.assign` med `{id, workflow_id, revision}` og printerens
forventede version. Kun publicerede `3d_print`-revisioner accepteres. Bindingen
fastlåses først for et fremtidigt job ved `Queue.reserve`; en ændring på en aktiv
printer ændrer ikke revisionen på et allerede reserveret job. Et køjob uden
printer kan vælge en ny binding, når `Queue.reserve` senere reserverer det.
Printere uden binding
forbliver i legacy-forløbet. Laserjob kan nu bindes til en aktiv station med en publiceret revision; bindingen starter ikke fysisk udstyr. Tilbageførsel af et
workflow ændrer ikke automatisk printerens binding; læreren skal vælge og
tildele den ønskede publicerede revision igen.

`printer.job.advance` kræver `{id, step_id, confirmed:true}` og
workflowjobbets aktuelle version. `produce` er lærerens godkendelse til
schedulerens næste trin og starter ingen maskine direkte. `collect` efterfølges
af lærerens `clear`; aborterede job går også til `clear`, og et afsluttet job kan
kræve et ekstra `prepare`-trin. Kameraresultater og legacy-frigivelse kan ikke
omgå disse regler. Kontrolpanelets bekræftelser kræver lærerens session,
også når trinets workflowrolle hedder `operator`.

## Agentindgang og lærerens administrationsfunktioner

Agentindgangen er dokumentation til en kodeagent. Den skal hurtigt forklare,
hvor en skole kan tilpasse navn, tema, netværk, digitale værksteder og
arbejdsgange, hvem der ejer data, og hvilke tests der skal bruges. Den giver
ingen programidentitet eller adgang til daglig drift. Kodeagentens arbejde
sker i skolens godkendte udviklingsmiljø efter skolens konkrete opgave.

`makerspace_api.py` er lærerpanellets HTTP-grænse. Den accepterer lærerens
session og afviser Authorization-tokens. Agentnøgleudstedelse, agentlisten,
tilbagekaldelsesruter og agent-CLI er fjernet. Skolens interne forbindelsesnøgler
til print- og lasertjenester bevares; de forbinder komponenter på serveren.

Lærerens ændringer har fortsat aktør, mål, forventet version og request-ID.
Genforsøg må ikke oprette ekstra maskiner eller starte et ekstra job.
Samtidige ændringer må ikke tavst overskrive hinanden. Revisionsloggen
bevarer historik uden adgangskoder eller unødvendigt elevindhold.

Gamle agenttabeller kan forekomme i tidligere udviklingsdatabaser. De giver
ingen adgang i den aktuelle kode. Backup bevarer dem som historiske data,
og gendannelse tilbagekalder deres gamle nøgler som beskyttelse ved senere
brug med ældre programkode. Nye databaser opretter ikke agenttabeller.

## Første milepæle og bevis for, at de virker

1. **Register og adaptergrænse:** eksisterende print/laser skal vises samlet, manuel
   station kan registreres, dubletter afvises, hemmeligheder udelades, ukendt
   status forbliver ukendt, og de gamle driftsforløb består deres tests.
2. **Workflowversioner:** en skabelon kan ændres og aktiveres; ugyldige
   overgange afvises; et eksisterende job bevarer sin version efter ændringen.
3. **Kontrolpanel og skoletilpasning:** lærerlogin, gentaget request-ID og
   forældet version er testet. Tidligere agenttokens giver ingen adgang.
   Kodeagentens Markdown forklarer arkitekturen og reelle tilpasningspunkter.
4. **Drift og gendannelse:** realistiske isolerede forløb afprøver genstart,
   offline/genforbindelse, backup/gendannelse og opdatering/tilbageførsel.
5. **Kreditering og deling:** Klostermarksskolen fremgår synligt og uafhængigt
   af den lokale skoles navn/tema. Kildeeksport, tredjepartsoversigt og
   byggevejledning kontrolleres, før en ny samlet pakke bliver aktuel.

Ingen af milepælene erstatter afprøvning på de konkrete fysiske maskiner,
Windows-installationer og Chromebook-modeller. Kravene
til offlineadfærd, databevarelse og afgrænset udrulning gælder fortsat.

## Print- og designtjenestens lokale projektbro

Projektserveren må indlejre elevens printside og overføre filbytes med en
forespørgselsbundet `postMessage`-håndtryksprotokol. Print og central Laser/Folie bruger
samme origin-liste til CSP `frame-ancestors` og browserens afsenderkontrol.
Lærersiden tillader aldrig indlejring. Tilladelsen ændrer ikke lærerlogin
eller print-API'ets kontrol af Host, Origin og CSRF.

Standard er `http://127.0.0.1:8791` og `http://localhost:8791`. En skole med
en anden lokal port kan lægge den private konfiguration i komponentens
datamappe, som vælges af installationen eller tjenestens `--data`:

| Tjeneste | Privat fil |
|---|---|
| Print | `app.folder/print-project-origins.json` |
| Central Laser/Folie | `data/laser-project-origins.json` |

Filerne hører til driftsdata og følger komponenternes backup/gendannelse.
De må ikke lægges ind i en installeret, låst programgeneration. Begge bruger
samme format:

```json
{"allowed_origins":["http://127.0.0.1:8793","http://localhost:8793"]}
```

Listen erstatter standardlisten. En tom liste slår indlejring fra. Kun
præcise HTTP-origins med eksplicit port 1–65535 for `127.0.0.1`, `localhost`
eller `[::1]` accepteres; sti, legitimationsoplysninger, query, fragment,
wildcards og LAN-/internetadresser afvises ved opstart. Listen indlæses før
HTTP-porten bindes. Isolerede integrationer kan give samme liste direkte
som `Server(..., project_origins=[...])` eller designets `create_server(...)`;
en særskilt konfigurationsfil kan vælges med `project_origin_config`.
Konstruktørerne uden disse argumenter bevarer programrodens filvalg til
eksisterende kildeintegrationer. De normale produktionsindgange giver
udtrykkeligt datamappens filsti; programroden er derfor ikke en
skoletilpasningsvej i en installeret generation. Browserens konfiguration
leveres som et eksternt script fra den respektive tjeneste under dens CSP.

En allerede kørende ældre backend kan læse nye HTML-/JavaScript-kildefiler,
mens den endnu returnerer 404 for konfigurationsscriptet. Når
`ElevhubProjectOrigins` mangler, bevarer modtageren derfor kun de to
historiske origins på port 8791. Det svarer til den ældre backends CSP;
andre porte eller adressepræfikser tillades ikke. En tilstedeværende liste
har altid forrang, også en tom liste; en ugyldig tilstedeværende værdi
tillader ingen afsendere. Backendens CSP gælder fortsat for indlejring.

`printer.update` bevarer eksisterende `location`, `enabled` og `bed_type`,
når de udelades. Eksplicit tom placering, `false` og en valgt pladetype
ændrer værdien. Navn, model, host og serienummer er fortsat krævede felter;
blank/udeladt LAN-adgangskode bevarer den eksisterende hemmelighed.

Ved lukning afbrydes igangværende softwarelagskæring og dens proces ventes
færdig, før datalåsen frigives. Et endnu ikke overført job går tilbage til
kø eller til sin eksisterende arbejdsgang med godkendelsen bevaret.
Dette er ikke en fysisk stopkommando til en printer, der allerede kører.

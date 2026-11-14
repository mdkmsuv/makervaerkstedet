# Sådan laver du en ny app til Makerværkstedet

Guide til en udviklingsagent, der skal bygge et nyt værksted (en webapp) til
elevenhederne. Skrevet 13. november 2026 ud fra koden på `codex/iso-apps`.
Retning og begrundelser står i [Værksteder som apps](APP-PLATFORM.md); denne
guide er arbejdsgangen. Stier er relative til `produkt/`. Guiden bruger ingen
linjenumre; søg efter de nævnte navne med `rg`.

Henvisninger ajourført 14. november 2026. **Denne guide beskriver den
nuværende integration i de fire faste Debian-pakker.** Den ønskede opdeling
i et hovedsystem og selvstændige tilvalgsapps er endnu ikke gennemført.
Git-struktur, selvstændige apppakker og tilvalg skal beskrives og bygges som
én sammenhængende ændring; en flyttet mappe giver ikke i sig selv app-tilvalg.

## Det vigtigste først

- **En app er en lokal webside på elevenheden.** Den åbnes fra startsiden
  «Makerværkstedet · Værksteder» i Chromium på `http://127.0.0.1:<port>/` og skal virke, når
  skolens server er væk, også efter kold opstart.
- **Serveren står kun for opdateringer, administration og køer.** En app må
  ikke hente sin brugerflade, sine data eller sine beregninger fra serveren.
  Eneste vej til serveren er en aflevering gennem udbakken.
- **Appen er endnu ikke en pakke, man bare lægger ind.** Pakke pr. app, tom
  ISO og skolens valg i kontrolpanelet er ikke bygget. En ny app registreres
  i dag ved kildeændringer de steder, tjeklisten nedenfor nævner, og følger
  med i `makervaerkstedet-elev`. Lav ikke en ny Debian-pakke uden særskilt
  aftale; alle pakker er obligatoriske i hver ISO og opdatering.
- **En app fra andre er et commit i projektets repo**, som gennemgås og
  godkendes af projektejeren. Der er intet åbent katalog endnu.

## Vælg den enkle vej

| Behov | Vej | Eksempel |
|---|---|---|
| Statisk webapp uden aflevering | Mappe med `index.html`, serveret af `elevhub-lokal` på egen port | Scratch, micro:bit (`linux-elevhub/projekter/lokal.py`) |
| Aflevering til laser- eller printkøen | Side i designtjenesten `elevhub-design` | Laser, folie, 3D (`app/laser_local.py`) |
| Ekstern hjemmeside | Hub-app af typen `web` med `start_url` og præcise `domaener`; ingen lokal kode | MakeCode bag micro:bit |

Begynd med den statiske vej, hvis du kan. Designtjenesten har faste
fillister og porte i koden og er dyrere at udvide. En ny afleveringstype
(andet end `laser-submit` og `print-job`) er en server- og
sikkerhedsændring og kræver særskilt aftale.

## Kontrakter appen skal overholde

**Lokalt og offline**

- Alle filer følger med; intet fra CDN. Kræver appen internet, sættes
  `kraever_internet` til `true` i manifestet, og kortet får mærket «Kræver
  internet».
- Tjenesten lytter kun på loopback. `Host` skal være `127.0.0.1:<port>`.
- Ingen skoledata, nøgler eller navne i koden. Brug syntetiske testdata.

**Navigation og afslutning**

- Sidens `<title>` må ikke begynde med `Makerværkstedet · Værksteder`; den
  tekst bruger elevbjælken til at genkende startsiden.
- «Til start» går tilbage i browserhistorikken. Brug ikke
  `location.replace` væk fra startsiden, og åbn ikke nye vinduer.
- Gemmer appen arbejde, skal den svare på afslutningsforespørgslen
  (`/_elevhub/session/poll` og `…/ack`), så eleven får gem/kassér, før
  bjælken forlader siden. Projektbroen gør det for dig, se næste punkt.

**Gem og genåbn**

- Definér `window.ElevhubProjectAdapter`, når editoren er klar; broen venter
  på adapteren. Påkrævet:
  `type`, `extension`, `name()`, `export()` (Blob eller Promise med Blob), `import(blob, name)` og
  `reset()`. Forbillede: `linux-elevhub/server/scratch/local-project-adapter.js`.
- Den lokale HTTP-tjeneste indsætter broen i appens HTML. Kilderne er
  `linux-elevhub/projekter/web/projekt-bridge.js` og
  `linux-elevhub/projekter/web/project-session.js`; URL'erne er
  `/_elevhub/projekter/bridge.js` og `/_elevhub/projekter/session.js`.
  Den viser den fælles projektdialog med klasse,
  fornavn og projektnavn, gem/åbn, og en gendannelseskopi hvert 15. sekund.
- `signature()` er valgfri, men skal være stabil for uændret projektindhold,
  hvis den bruges. Uden den sammenligner broen de eksporterede bytes.
  `hasContent()` kan beskytte arbejde, som allerede var åbent ved opstart.
  `elevhub-project-changed` bruges ved et nyt/erstattet projekt uden om
  projektbroen; udsend det ikke ved hvert tastetryk, da det ophæver den
  eksisterende projektbinding. Se den faktiske håndtering i `project-session.js`.
- `reset()` skal efterlade en tom app til næste elev. Forrige elevs projekt
  må aldrig åbne af sig selv.
- Projekter slettes efter 30 dage uden gemte ændringer; højst 50 MiB pr. fil.
- En ny projekttype skal tilføjes de faste lister i
  `linux-elevhub/projekter/` (`workshop_sessions.py` `KINDS`,
  `projektarkiv.py` `TYPES` og `EXTENSIONS`, `filtyper.py`,
  `project_open.py`, `scanner.py`, `linux-elevhub/projekter/web/projekter.js`).

En app uden projektlagring er endnu ikke en generel tilvalgsvej i denne
værkstedsvært: den indsatte projektbro venter på en adapter. Et eksplicit
fravalg af projektflowet indgår i den planlagte fælles appkontrakt.

**Aflevering til køerne**

- Siden sender til sin egen loopback-adresse; den lokale tjeneste
  videresender til serveren. Appen taler aldrig selv med serveren.
- `laser-submit`: `POST /api/laser/submit` med JSON
  `{elevnavn, klasse, projekt, svg, requestId}`. `print-job`:
  `POST /api/jobs` med binær STL og `X-Request-ID`.
- Svar **202** `{"queued": true}` betyder «venter på forbindelse».
  Udbakken sender igen med samme `requestId`. Sig det på skærmen: sendt,
  venter og fysisk færdig er tre forskellige ting.
- 423 betyder, at læreren har lukket værkstedet; 400/413 er en afvisning,
  som eleven skal se som en dansk besked.

**Brugerflade**

- Følg [UI/UX-kravene](UI-UX-KRAV.md): elever fra 2. klasse, dansk
  klarsprog, WCAG 2.2 AA, klikmål på 44 × 44 px, tastatur og synligt fokus,
  intet der kun kan gøres ved at trække.
- Vurdér ved 1024 × 688 og 1024 × 768. Elevenhedens skærm er lille.
- Brug skolens tema: `web/school-theme.css` og `web/school-theme.js` giver
  `--brand` og `--brand-dark`. Skriv ikke egne mærkefarver ind i appen.
  Skriften er Metrophobic (`web/assets/metrophobic-latin.ttf`).
- Alle UI-ændringer skal vurderes af en UI/UX-specialist efter
  [rollekortet](agenter/UI-UX-AGENT.md), i renderet tilstand.

**Licens og kreditering**

- Egne dele er GPL-3.0-or-later; `linux-elevhub/` er MIT. Tredjepart
  beholder egen licens og skal med i [CREDITS.md](../CREDITS.md) og pakkens
  licensfiler. Se [licensafgrænsningen](../LICENSE-SCOPE.md).
- Projektets kreditering skal være synlig i appen og må ikke kunne fjernes
  af skolekonfigurationen.

## Tjekliste: registrér en statisk app

Eksemplet er en app med id `X` og port `P`. Vælg en ledig port i
8790–8799-området (optaget: 8790–8798) eller en anden over 1024, og brug
samme `id` alle steder; id'et er små bogstaver, tal og bindestreg.

1. **Appens filer.** Læg dem i en kildemappe med `index.html`, som de andre
   lokale apps under `linux-elevhub/server/`. Byggetrin tilføjes i
   `package.json`, hvis appen skal bundtes.
2. **Lokal servering.** Tilføj `X: P` til `APPS` i
   `linux-elevhub/projekter/lokal.py`, og få filerne med i pakken i
   `linux-elevhub/packaging/prepare_inputs.py` (se hvordan `microbit`
   medtages).
3. **Manifest.** Tilføj en række i `APP_MANIFESTS` i samme
   `prepare_inputs.py`: id, navn, start-URL, `offline`, `aflevering` og
   `kraever_internet`. Feltreglerne står i
   `linux-elevhub/projekter/app_manifests.py`.
4. **Hubbens katalog.** Tilføj appen i
   `linux-elevhub/server/elevhub/konfiguration.json` og
   `windows-elevhub/server/hub/konfiguration.windows.json` som type `lokal`
   med `port` og `lokal_service: true`. Uden den vises kortet kun offline,
   og læreren kan ikke åbne eller lukke appen.
5. **Tilladte lokale adresser.** `APP_URLS` og standardportene i
   `linux-elevhub/administration/template_activation.py`, samt
   `linux-elevhub/ansible/roles/kiosk/defaults/main.yml` og
   `linux-elevhub/ansible/roles/kiosk/tasks/main.yml`. Ellers
   blokerer browserpolitikken siden, når læreren har lukket internettet.
6. **Chromium-politik.** Porten i `InsecureContentAllowedForUrls` i
   `linux-elevhub/ansible/roles/kiosk/templates/chromium-politik.json.j2`.
7. **Klar-kontrol efter opdatering.** `ENDPOINTS` i
   `linux-elevhub/administration/runtime_checks.py`; en manglende port får
   opdateringen til at fejle.
8. **Kortet på startsiden.** Tekst, gruppe (`byg`, `kode` eller `ide`),
   billede, trin og idéer i
   `linux-elevhub/server/elevhub/web/assets/workshops.js`. Uden det får
   appen kun et standardkort uden detaljeside.
9. **Offentlig kilde.** Nye filer i
   `deployment/public-source-manifest.json`; kør
   `python -B scripts/public_source.py --check`.
10. **Dokumentation.** Opdatér statustabellen i
    [APP-PLATFORM.md](APP-PLATFORM.md), og tilføj licens i `CREDITS.md`.

En app i designtjenesten kræver desuden port og sider i
`app/laser_local.py`, de faste fillister i `app/laser_server.py` og
`linux-elevhub/ansible/roles/kiosk/tasks/main.yml` (`kiosk_lokal_design_filer`,
som læses med et regulært udtryk; bevar formatet) og
`linux-elevhub/ansible/roles/kiosk/templates/elevhub-design.service.j2`.

## Afprøvning

Kildelæsning beviser ikke, at appen kan ses, klikkes eller gemmes.

- **Enhedstests du skal udvide:** `linux-elevhub/tests/test_app_manifests.py`,
  `test_app_manifest_packages.py` (forventede apps står som fast liste),
  `test_internetregler.py`, `test_workshop_launcher.py` og, hvis appen
  gemmer projekter, `test_lokale_projekter.py` og
  `test_project_opening.py`. På hub-siden
  `linux-elevhub/server/elevhub/test_vaerksteder.py`.
- **Startsiden i browser:** `test_kiosk_manifest_browser.cjs`,
  `test_kiosk_offline_catalog.cjs` og `test_kiosk_offline_reconnect.cjs`.
- **Kendte fejl:** se `tests/KENDTE-FEJL.txt`, før du tolker en rød test som
  din egen.
- **Byg fra den valgte, frosne kilde** i Debian 13 amd64, fx WSL. Følg
  [pakkernes byggekontrakt](../linux-elevhub/packaging/README.md) og
  [udgivelsesprocessen](UDGIVELSESPROCES.md): verificerede inputlocks,
  `build_release.py` og faktisk apt-simulering af de fire præcise pakker.
  Hjælperen `linux-elevhub/packaging/build_from_source.sh` bygger fra
  `git archive HEAD`; ikke-committede appfiler kommer ikke med. Hjælperens
  historiske Scratch-inputnavne skal afstemmes med de verificerede byggeinputs.
- **Installeret prøve:** installér pakkerne på en ikke-tilmeldt testmaskine
  og kør forløbet i en rigtig browser ved 1024 × 688: udfyld klasse, navn og
  projektnavn, lav noget, gem, gå til start, genåbn, og kontrollér at næste
  elev får tomme felter. Genbrug
  [udgivelsesprøverne i repoet](../scripts/release_qa/README.md):
  `install_student_packages.py`, `installed-laser.cjs`, `installed-folie.cjs`
  og `installed-3d.cjs` under `scripts/release_qa/`. De dækker de eksisterende
  apps; en ny app skal have et tilsvarende faktisk browserforløb.
- **Offline:** stop forbindelsen til serveren og gentag forløbet, også
  efter genstart af enheden. En aflevering skal ende i udbakken og sendes,
  når serveren er tilbage.

## Færdig betyder

- Kortet vises på startsiden både med og uden server, og læreren kan åbne
  og lukke appen i kontrolpanelet.
- Gem, «Til start», genåbn og elevskift virker i installeret tilstand.
- Appen virker efter kold opstart uden server.
- UI/UX-review er gennemført på den renderede app.
- Tests, licenser og den offentlige kildekontrol er opdateret.
- Afleveringen skelner mellem kilde, bygget pakke og testinstallation.
  Installation på skolens udstyr og publicering kræver egen bestilling.

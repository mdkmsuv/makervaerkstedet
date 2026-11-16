# Værksteder som selvstændige apps

Kildearkitektur ajourført 16. november 2026. Hovedsystem, apps og SDK er
fysisk adskilt i Git. Dette dokument beskriver kildekontrakten; en ny
modulær installationsudgivelses native installation, migration og ISO-boot
skal fremgå af dens egen kvittering. En eksisterende EXE/ISO med
versionsslutning `.2` får ikke disse ændringer ved at kilden ændres.

## Ejerskab og versionsgrænser

| Kilde | Ansvar | Pakke |
|---|---|---|
| `system/` | Lærerpanel, enhedsidentitet, projekthylde, lokal appvært, køgateways og opdaterer | `makervaerkstedet-elev`, `makervaerkstedet-updater` samt Windows-server |
| `apps/<id>/` | Appens brugerflade, descriptor, præcis filliste og egne byggeinputs | `makervaerkstedet-app-<id>` |
| `sdk/` | Opretter, kontrollør, lokal prøve, skabeloner og integrationskontrakter | Udviklingsværktøjer |
| `deployment/`, `scripts/`, `tests/` | Opsætning, udgivelsesværktøjer og fælles kontrol | Udvælges af den relevante bygger |

De fem medfølgende appkilder er `laser`, `folie`, `3dprint`,
`scratch` og `microbit`. En app ændrer sin egen version, descriptor og
artefakt. Det kræver ingen ny hovedsystemversion eller registrering i en
fast liste. `REPOSITORY-LAYOUT.json` beskriver kildernes placering og de
gamle logiske sti-aliaser. Alias er kompatibilitet i byggeværktøjer,
ikke en ekstra kildekopi.

Appens `app.json` har schema 2 og binder identitet, pakkenavn, version,
arkitektur, SDK-version, mindste kerne, loopback-startadresse, offline- og
internetkrav, projektformat og capabilities. `files.json` afgrænser
ejerskabet til appens egen kildemappe og installerede træ under
`/usr/share/makervaerkstedet/apps/<id>/`. Kernen importerer ikke appkode.
Installerede systemstier og eksisterende projekt-/datastier bevares;
flytning i Git er ikke en flytning af elevens filer.

Se [ny-app-guiden](NY-APP-GUIDE.md) for de præcise SDK-, bygge- og
udgivelseskommandoer og [kodekortet](KODEKORT.md) for systemets filer.

## Elevens indgang og projekter

[Scratchs appbeskrivelse](../apps/scratch/README.md) og
[3D-værkstedets appbeskrivelse](../apps/3dprint/README.md) forklarer lokal
gemning og 30-dagesreglen for elever og skoler. Autogem ejes af appen;
oprydning efter inaktivitet ejes af elevcomputerens fælles projekthylde, så
den fortsætter, selv om appen ikke åbnes eller er afinstalleret.

Startsiden bygger sine værksteder fra faktisk installerede, gyldige
descriptors. Appvært, projektåbning og lokale tjenester følger samme
registrering. En tom basis kan derfor vise start, Internet og projekthylde
uden et installeret værksted. Et projekt til en fjernet app bevares;
åbning kræver, at den passende app igen er installeret.

**Installeret** og **åben i undervisningen** er forskellige tilstande.
Appkataloget ændrer softwarebestanden. **Apps og internet** styrer
lærerens aktuelle adgang. En lukning afinstallerer intet. Ved tab af
serverforbindelsen åbner de installerede lokale apps og Internet; en app
med eksterne tjenester kan stadig kræve internet. Micro:bit har en lokal
indgang, mens MakeCode kræver netværk.

Appen kan være uden lagring eller deklarere et typet projekt.
`projects-v1` giver den eksisterende projekt-/sessionbro: navngivning,
gem, genåbn, revisioner og elevskift. Adapteren validerer egne opaque
bytes før import og nulstiller indhold og navn ved elevskift. Den forrige
elevs dokument åbnes ikke automatisk. Systemets øvre filgrænse er 50 MiB;
appen må vælge en lavere.

### Autogem tilhører appen og virker lokalt

Appspecifik autogem implementeres og versionsstyres i `apps/<id>/`, sammen
med appens ændringsregistrering, gemmetidspunkt og visning af gemmestatus og
fejl. Scratchs autogem hører derfor til `apps/scratch/`. Hverken skolens server
eller appkataloget skal styre autogem eller modtage løbende projektindhold.

Appen må genbruge den fælles projekt-/sessionbro og den **lokale** projekthylde
til navngivning, selve skrivningen, revisioner og elevskift. Denne tjeneste
kører på elevcomputeren; den er ikke skolens server. Appens autogem må ikke
kræve forbindelse til skoleserveren, en cloudkonto eller nye appregler i
serverkoden. Særskilt bestilt aflevering til en kø er en anden handling.

**Scratch og 3D fra core `2.1.0~20261116.1`:** appens `web/autosave.js` gemmer
navngivne projekter i den lokale projekthylde, også første gang før en manuel
gemning. Ændringer kontrolleres cirka hvert femte sekund; aktiv redigering,
import og elevskift afsluttes først. Eleven venter på **Gemt på denne computer**
og genåbner senere gennem **Find mit projekt** på samme enhed. Autogem hverken
indlæser projektet igen eller tømmer editorens Fortryd-/Gentag-historik.

Gentagne autogemninger erstatter den umiddelbart forrige automatiske
arbejdsudgave, så hver kontrol ikke ophober en fuld projektkopi. Revisionen
stiger stadig, og alle manuelle gemninger bevares. **Gem projekt** bevarer
også en manuel version af uændret indhold. En tidligere auto før en manuel
gemning med andet indhold kan blive stående; der ryddes ikke generelt i historik.
Åbning af en tidligere version sætter autogem på pause. Den nyeste arbejdsudgave
bevares, indtil eleven selv vælger **Gem projekt** eller **Gem kopi**; dette
gælder også, hvis den gamle version gendannes via browserens sikkerhedskopi.

Projektbroens `saveState()` og `save({background:true,expectedNaming})` er kun
generiske lokale sikkerhedsfunktioner. Timer, ændringskontrol og brugerstatus
ejes stadig af hver app. Den særskilte 15-sekunders browserkopi til gendannelse
er fortsat adskilt fra projektarkivet. Kildeprøver, bygget pakke og faktisk
installation skal fortsat dokumenteres særskilt; disse ændringer findes ikke
automatisk i de ældre signerede pakker eller ISO'er.

## Afgrænsede capabilities

Aktuel SDK er 1, nye apps kræver mindst core `2.1.0~`. Capabilities
deklareres som `required` eller `optional`; ukendte nødvendige
capabilities afvises.

| Capability | Adgang |
|---|---|
| `projects-v1` | Fælles typede projekt- og sessionfunktioner |
| `theme-v1` | Valideret skoleidentitet, logo, farver og lokal skrifttype |
| `svg-library-v1` | Det afgrænsede SVG-bibliotek; ingen kørettighed |
| `laser-submit-v1` | Den eksisterende lokale laseraflevering |
| `print-job-v1` | Den eksisterende lokale STL-aflevering |

Der er ingen shellkommandoer, vilkårlige serveradresser, installationshooks
eller nye brugeridentiteter i manifestet. Skolens nøgler og elevdata er
installationsdata og indgår aldrig i apppakken.

## Køer og arbejde uden forbindelse

Serveren ejer printkø og laser-Inbox. Appen kan valgfrit bruge en lokal
gateway, som validerer og bevarer afleveringer i en udbakke. Udbakken
overlever browserlukning og genstart, også når den afsendende app fjernes.
Samme konkrete aflevering beholder sit `requestId` under genforsøg.

- **202, queued: true** betyder gemt lokalt og afventende forbindelse.
- **Modtaget i køen** kræver en konkret kvittering fra serveren.
- Fysisk udførelse og færdigmelding følger lærerens maskinflow.

En afvisning skal bevare elevens arbejde; lærerens lukning er ikke en
netværksfejl. SDK'ets køeksempel må prøves med mocks eller aftalt testmiljø.
En rigtig aflevering er en reel køhandling.

## Godkendt katalog og lærerens valg

Udgiveren bygger én app, kontrollerer de native pakkefelter og binder
descriptor, kildecommit, størrelse og SHA256 i et minisign-signeret
appkatalog. Apt-repositoryet har sin særskilte native GPG-signatur.
Eksisterende nøgler, repositoryidentitet og tillidsankre genbruges.

I **Appkatalog** ser læreren installerede og tilgængelige apps med den
rapporterede tilstand pr. computer, også blandede versioner og ukendt eller
offline status. Læreren vælger app og computere og bestiller
**Installér**, **Opdatér** eller **Afinstallér**. En bestilling er ikke en
færdig installation. Ved ukendt udfald afklares samme bestilling med samme
id; en ny bestilling må ikke skabe en dublet.

Skolens standardsæt gælder nye tilmeldte enheder. Det installerer ikke
tavst apps på alle eksisterende computere. En afinstallation bevarer
projekter, lokal skoleprofil og ventende afleveringer.

## Basis og overgang fra den gamle samlede kerne

Den modulære basis består af tre pakker: elevkerne, opdaterer og Matchbox.
Et app-byggesæt er basis plus én app; et migrationsbyggesæt indeholder
basis plus alle fem indbyggede apps. Byggesættets otte pakker er ikke et
krav om permanent installation af alle apps.

Overgangen skal ske i rækkefølge: bro-opdaterer først, derefter verificeret
split-kerne og bevarede apps i én afgrænset native apt-transaktion.
Eksisterende Laser, Folie, 3D og micro:bit bevares; Scratch følger kun med,
hvis det faktisk var installeret. Skolens standardvalg erstatter ikke den
gamle enheds bestand. Afbrudt migration skal genoptage og kontrollere
appbestanden, selv hvis kernens versionsnummer allerede matcher.

Bro-opdatereren kræver kun svar fra Scratch, hvis pakken faktisk er
installeret. Den gamle 1.0-opdaterer kan efter udskiftning til broen endnu
rapportere `runtime_endpoint_unavailable`, fordi dens allerede indlæste kode
kræver Scratch. Næste automatiske kørsel bruger den nye opdaterer og
genoptager aktiveringen uden at installere Scratch eller hente pakkerne igen.
En ny bestilling er ikke nødvendig. Andre manglende tjenester bliver stadig
rapporteret som fejl.

Ved en vedvarende migrationsfejl åbner læreren **Teknik → Opdateringer**,
finder computerens seneste resultat og vælger **Stop genforsøg** på den
pågældende computer. Serveren trækker kun det afventende forsøg tilbage.
En apt-installation, som allerede kører på computeren, afbrydes ikke;
installerede pakker, projekter og fejlhistorik bevares. Vent på en ny
rapport fra computeren, ret årsagen og bestil først derefter igen.
`insufficient_migration_space` kræver mere ledig diskplads;
`migration_installed_set_changed` kræver kontrol af de faktisk installerede
apps. `dpkg_manual_repair_required` kræver teknisk reparation af den
ufuldstændige pakkeinstallation, før nye pakkehandlinger kan gennemføres.
Genindlæs status, hvis en anden lærer allerede har ændret bestillingen.

Den historiske schema-1-coreprotokol bevares; den får ikke et schema-2-
appkatalog. Nye appordrer bruger den separate autentificerede appkanal.
Se [apt-kontrakten](APT-REPOSITORY.md) og
[udgivelsesprocessen](UDGIVELSESPROCES.md) for kvitteringer og native prøver.

## Skoleprofil og SVG-bibliotek

Læreren kan forhåndsvise og gemme navn, PNG-logo, temafarve og få lokale
skrifttyper i **Skoleprofil**. Den administrerede profil distribueres via
den tilmeldte servers HTTPS/CA, verificeres og gemmes lokalt til offline
brug. En tidligere privat profil beholdes, indtil læreren gemmer den nye.
Se [skoletilpasning](SKOLETILPASNING.md).

Både Laser og Folie åbner SVG-biblioteket direkte fra den faste
Skoleværkstedet-hjemmeside gennem den afgrænsede gateway. Cache er lokal,
ikke en kopi publiceret af hovedserveren. Sidst hentede katalog og figurer
kan bruges uden forbindelse; figurer, der aldrig er hentet, er ikke
garanteret offline. Fladen viser, når cachen er gammel eller tom.

Bevar [kreditering](../CREDITS.md), [licensafgrænsning](../LICENSE-SCOPE.md)
og hver apps tredjepartslicenser ved ændring og distribution.

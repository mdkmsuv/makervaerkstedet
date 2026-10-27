# Offentlig kildeudgave

Denne vejledning beskriver, hvordan Makerværkstedet eksporteres til en neutral
GitHub-kildeudgave. Eksporten er et kildegrundlag; en versioneret installations-
eller downloadrelease skal bygges og kontrolleres særskilt.

Den offentlige udgave bruger de generiske Bauhaus-inspirerede skins. Skolelogo,
Klosterbrandstyle, private profiler, skoleinventar, elevdata, lokationsdata,
SSH-nøgler, certifikater, kødatabaser, træningsbilleder og lokale driftslogge
skal blive i den lokale opsætning. Klostermarksskolens og Mathias Kusk
Damgaards kreditering skal bevares, og tredjepartskomponenters egne licenser
og notices skal følge med. Se [CREDITS](../CREDITS.md),
[licensafgrænsning](../LICENSE-SCOPE.md) og
[release-licenser](RELEASE-LICENSER.md).

Eksportér fra projektroden til en ny mappe uden for rodtræet:

```powershell
python -B scripts/public_source.py --check
python -B scripts/public_source.py --output D:\Makervaerkstedet-source
```

Brug den kontrollerede allowlist i exporterens policy. Eksporten afviser
eksisterende output, private nøgler, runtime-data, links/junctions, ukendte
filtyper og for store filer. Den kontrollerer kreditering, lokale links og
ankre samt SHA-256 under og efter kopiering. `PUBLIC-SOURCE-MANIFEST.json`
registrerer de kopierede filer, størrelser, hash og policyhash. Gennemgå den
færdige kopi manuelt før den eventuelt bliver et repository.

```powershell
python -B scripts/public_source.py --check-repository
```

Kildekontrollen godkender ikke færdige ZIP-filer, ISO'er, skoleprofiler eller
installerede datamapper. Byg pakker fra den neutrale kilde med eksplicitte,
hashbundne inputs. En almindelig commit er ikke en installationsbestilling;
versionerede releases skal kunne identificeres og bekræftes som installeret.

Produktet er Windows-serveren med lærerens kontrolpanel og Linux-
elevværkstederne. Den historiske Windows-elevklient er ikke en understøttet
offentlig klient. Agentindgangen er Markdown-dokumentation for kodeagenter;
der skal ikke medfølge agentnøgler, agentpanel eller en driftsagent.

## Licenser og aktiver

Projektets egne nye dele følger GPL-3.0-or-later med kreditering. Elevhubs
eksisterende MIT-komponenter og øvrige tredjepartsdele følger deres egne
vilkår. Medtag notices, kildehenvisninger, copyrighttekster og de nødvendige
kontrolsummer. Skolelogoer og skolebundne referencefotos er ikke generiske
offentlige aktiver.

## Bygning fra en ren kildeudgave

Installer kun de versionsbundne udviklingsafhængigheder, der er beskrevet i
projektets requirements- og package-filer, og brug den relevante
komponents byggevejledning. Test på den platform, som pakken målrettes; en
platformskip er ikke en bestået test. Manglende eksterne bundler eller
inputs skal rapporteres som manglende input.

Windows-builderen skal have et eksplicit artifact-manifest med versionsstreng
og SHA-256 for hver mappe. Manifestet må ikke pege på private arbejdsmapper,
lokale caches, skoledata eller skjulte pilotinputs. Et input, der ændres,
kræver ny hash- og licenskontrol. AI til plade- eller laservurdering,
modelvægte og inference-runtimes må ikke indgå i denne udgave. Byggeren
har ingen tilvalg af drifts-AI. Datatræner er et særskilt undervisningsværktøj
og indgår fortsat. SkoleGPT bevares som eksternt undervisningslink.

```powershell
python .\windows-elevhub\build_packages.py --role server --output .\staging\server `
  --artifact-manifest .\artifacts\windows-artifacts.json
python .\scripts\build_school_package.py --output .\staging\school --role vm `
  --artifact-manifest .\artifacts\windows-artifacts.json --school-name "Eksempelskolen"
```

En generisk pakke må ikke automatisk kopiere skoleinstruktioner,
skoleprintere, skolelogoer eller lokale driftsresultater. Store runtime- og pakkeassets skal holdes ude af kildehistorikken eller leveres som
versionerede downloads med kontrolsummer og licensafklaring.

## Før udgivelse

1. Kontroller allowlisten, krediteringen, tredjepartsnotices og branding.
2. Byg fra en ren kildekopi med eksplicitte, hashbundne inputs.
3. Kør relevante kilde- og browserprøver på deres målplatforme.
4. Kør installationsforløbets skrivefri kontrol og dokumentér den valgte
   releaseversion.
5. Afprøv en Windows-server og en Debian amd64-elevcomputer på aftalt udstyr;
   skil kildeprøver fra faktisk installeret drift og hardware.


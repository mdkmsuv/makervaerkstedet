# Agentindgang til Makerværkstedet

Makerværkstedet er et system til skolers makerspace med læreradministration,
printer- og laserforløb samt digitale elevværksteder. Denne fil hjælper en
kodeagent med installation, skoletilpasning og videreudvikling.

## Start her

- [Kom godt i gang](docs/KOM-GODT-IGANG.md): vælg installation eller opdatering.
- [De to opsætninger](docs/TO-OPSAETNINGER.md): fælles Windows-computer eller
  Windows-server med separat laserstation. Elevcomputere bruger Linux.
- [Agentvejledning](docs/AGENTINDGANG.md): arkitektur, relevante kildefiler og tests.
- [Skoletilpasning](docs/SKOLETILPASNING.md): lokal skoleprofil, tema og netværk.
- [GitHub-opdateringer](docs/GITHUB-OPDATERINGER.md) og
  [backup](docs/MAKERSPACE-BACKUP.md): versionsskift og databevarelse.

## Ved installation og ændringer

- Afklar målcomputerens rolle og om opgaven er ny installation, opdatering
  eller gendannelse. En kodeændring er ikke en installation.
- Bevar lokale skoleprofiler, temaer, nøgler, elevprojekter, maskinopsætning,
  køer og historik ved opdatering. Brug ikke ren installation som opdatering.
- Læreren styrer den daglige drift gennem kontrolpanelet. Kodeagenten har
  ingen særskilt driftskonto eller skjult adgang til skolens udstyr.
- Brug syntetiske data i tests. Del ikke skolers data, nøgler eller lokale
  tilpasninger i kildekode, eksempler eller installationspakker.
- Kør relevante tests for ændringen. Skeln mellem kildeprøver, byggede pakker
  og faktisk afprøvning på det pågældende operativsystem og udstyr.
- Ved serverudfald skal elevværksteder, CaDoodle og Internet være åbne;
  lærerens aktuelle valg gælder igen, når forbindelsen vender tilbage.
- Bevar [ophavskreditering](CREDITS.md), [licensafgrænsning](LICENSE-SCOPE.md)
  og tredjepartslicenser, også når en skole vælger eget navn og tema.

Offentlige kildeudgivelser følger filudvalget i
`deployment/public-source-manifest.json`. Kontrollér det med
`python scripts/public_source.py --check-repository` før udgivelse.

# Makerværkstedet

Makerværkstedet er lærerens kontrolsystem til skolens makerspace: maskiner,
Linux-elevcomputere, værksteder, workflows og fælles køer. Windows bruges til
hovedsystemet og lærerens browseradgang; elevværkstederne understøttes på
Linux. Systemet kan køre på én Windows-computer eller med en central
Windows-server/VM og en separat Windows-lasercomputer.

Start med [Kom godt i gang](docs/KOM-GODT-IGANG.md). Vælg dér mellem ren
installation og opdatering af Linux-elevcomputer eller Windows-server.
Installationsforløbet forklarer felter, forudsætninger, databevaring,
genoptagelse og næste skridt ved fejl. En ren installation opretter et nyt
system; en opdatering skal bevare aftalte projekter, identitet, tilmelding,
nøgler, konfiguration, køer og historik. Backup, gendannelse og genoptagelse
er særskilte handlinger.

Vigtige indgange:

- [To opsætninger](docs/TO-OPSAETNINGER.md)
- [Lærerens kontrolpanel](docs/SERVERKONTROLPANEL.md)
- [Vedvarende opdateringskø](docs/OPDATERINGSKOE.md)
- [Backup og gendannelse](docs/MAKERSPACE-BACKUP.md)
- [Skolekonfiguration](docs/SKOLETILPASNING.md)
- [Arkitektur](docs/MAKERSPACE-ARKITEKTUR.md)
- [Printerfrigivelse](docs/PRINTERFRIGIVELSE.md)
- [Licenser og kreditering](CREDITS.md), [licensafgrænsning](LICENSE-SCOPE.md)

Læreren styrer den daglige drift gennem kontrolpanelet. Browseren sender ikke
private SSH-nøgler, scripts eller vilkårlige serverstier. Nye maskiner og
workflows registreres i administrationen; en registreret maskine er ikke
dermed automatisk understøttet hardware. En laserhandling giver lærerens
tilladelse til næste trin, men starter ikke fysisk udstyr uden den særskilte
stationsintegration.

Når serveren ikke kan nås, skal værksteder, CaDoodle og Internet være åbne,
også efter kold opstart. Når forbindelsen vender tilbage, bruges lærerens
aktuelle valg igen.

Den offentlige kildeudgave bruger generiske Bauhaus-inspirerede skins.
Skolelogo, Klosterbrandstyle og andre lokale skoleprofiler hører til den
lokale opsætning. [Public release](docs/PUBLIC-RELEASE.md) beskriver eksport,
privat konfiguration og tredjepartsnotices. En almindelig commit er ikke en
installationsbestilling. Der er endnu ingen installationsrelease verificeret
fra GitHub.

GitHub skal være hele grundlaget for installation og opdatering, så den
oprindelige udviklingsmappe kan undværes. Programmet kører lokalt på skolens
computere. Skolens skin, opsætning, nøgler, projekter og køer bliver i den
lokale installation og dens backup. Se
[drift uden udviklingsmappen](docs/GITHUB-OPDATERINGER.md#drift-uden-udviklingsmappen)
for krav til byggeinputs og afprøvning; kildekoden alene er ikke en færdig
installationsudgivelse.

Denne udgave indeholder ingen AI til pladekontrol, laseranalyse eller anden
maskinstyring. Læreren vurderer kamerabilleder og frigiver plader manuelt.
Datatræner bevares som undervisningsværktøj i maskinlæring, og SkoleGPT som
eksternt undervisningslink. Projektets egne nye
dele følger GPL-3.0-or-later; se [LICENSE](LICENSE) og
[licensafgrænsningen](LICENSE-SCOPE.md). Produktet er udviklet af Mathias
Kusk Damgaard med kreditering til Klostermarksskolen. Medfølgende
tredjepartskomponenter beholder deres egne licenser.

# Produktindgang — Makerværkstedet

Arbejdsform og mandat følger projektrodens `AGENTS.md` (mappen over denne):
hovedagenten udvikler selv, højst tre underagenter og UI/UX-review ved alle
UI-/flowændringer. Den gamle faste agentfordeling gælder ikke længere.
Denne fil supplerer reglerne og genindfører ikke et generelt installationsforbud.
Arbejdsmapper under 2.0 følger også projektrodens regler.

## Læs kun det relevante

| Opgave | Reference |
|---|---|
| UI eller brugerflow | [UI/UX-agent](docs/agenter/UI-UX-AGENT.md), relevante [krav](docs/UI-UX-KRAV.md) |
| Ny app/nyt værksted til elevenhederne | [Sådan laver du en ny app](docs/NY-APP-GUIDE.md) |
| Arkitektur, kildefiler og testkommandoer | [Agentindgang](docs/AGENTINDGANG.md) |
| Installation/opdatering og maskinrolle | [Kom godt i gang](docs/KOM-GODT-IGANG.md), [to opsætninger](docs/TO-OPSAETNINGER.md) |
| Skoleprofil, tema og netværk | [Skoletilpasning](docs/SKOLETILPASNING.md) |
| Faktisk pakke, udgivelse eller udrulning | Relevante dele af [udgivelsesprocessen](docs/UDGIVELSESPROCES.md) |
| Backup, gendannelse eller versionsskift | [Backup](docs/MAKERSPACE-BACKUP.md), [opdateringer](docs/GITHUB-OPDATERINGER.md) |

## Produktgrænser

- Afklar ny installation, opdatering eller gendannelse. Bevar lokale profiler,
  temaer, nøgler, projekter, maskinopsætning, køer og historik ved opdatering,
  medmindre den konkrete bestilling ændrer bevaringskravet.
- Brug syntetiske testdata. Skoledata, nøgler, private skins og rå driftslogs
  må ikke indgå i generisk produktkode, eksempler eller pakker.
- Læreren styrer fysisk drift. Ingen skjult driftskonto eller test, der
  automatisk starter maskiner eller kvitterer en fysisk ryddet plade.
- Ved serverudfald skal lokale elevværksteder og Internet-adgang være åbne;
  eksterne tjenester kræver stadig netværk. Lærerens aktuelle valg gælder
  igen ved forbindelse.
- Bevar [kreditering](CREDITS.md), [licensafgrænsning](LICENSE-SCOPE.md)
  og tredjepartslicenser ved genbrug og skoletilpasning.
- En udgivelse skal kunne genskabes fra dokumenteret kilde. Windows-
  udgivelser følger relevante installations- og opdateringsprøver i
  udgivelsesprocessen, ikke ved hver lille kilde- eller dokumentrettelse.
- Før offentlig kildeudgivelse kontrolleres deployment/public-source-manifest.json
  med den eksisterende offentlighedskontrol. En almindelig udviklingsbestilling
  er ikke i sig selv en bestilling om publicering.

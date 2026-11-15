# Lommeregner

**Testapp og SDK-eksempel.** Bruges til at afprøve appguiden, katalogudgivelse
og installation/afinstallation i QA-miljøet. Den indgår ikke i skolens
standardinstallation og er ikke bestilt som en produktionsapp.

En dansk, lokal lommeregner til elevkiosken. App-id: `lommeregner`, version
`1.0.0`, loopback-port `8892`, SDK 1. Der er intet bundlingtrin:
`web/` indeholder de færdige statiske programfiler.

## Adfærd

- Plus, minus, gange og division, decimalkomma og decimaltast med punktum.
- Regner ét trin ad gangen fra venstre mod højre som en enkel lommeregner:
  `2 + 3 × 4 =` giver `20`. En ny regneart erstatter en endnu ikke udført.
- `+/−` skifter fortegn, `Slet` fjerner sidste ciffer, og `Ryd` starter forfra.
- Højst 12 indtastede cifre; resultater afrundes til 12 betydende cifre.
  Meget store/små tal kan vises med eksponent, fx `1e−11`.
- Division med nul bevarer det første tal og regnearten. Skriv en ny nævner
  og tryk `=`. Et tal uden for det endelige talområde forklares og kræver Ryd.
- Et nyt ciffer efter et resultat starter et nyt regnestykke. En regneart
  efter resultatet fortsætter med resultatet. Gentagne `=` gentager ikke
  automatisk den sidste operation.
- Tastatur: cifre, `,`, `.`, `+`, `-`, `*`, `/` og `=`. Backspace sletter,
  Escape/Delete rydder. Enter aktiverer en fokuseret knap; uden knapfokus
  beregner Enter. Tab, Enter og mellemrum følger almindelige HTML-knapper.
- Der er ingen gemning, projektadapter, cookies, browserlagring eller
  elevkonti. Genindlæsning starter ved 0.

Appen bruger den eksisterende `theme-v1`-kontrakt, `--brand`, `--brand-dark`,
`data-school-name`, `.brand` og `.brand-mark`. Metrophobic, skoleprofil og
logo leveres af den lokale vært; der følger ingen skoleprofil med appen.
Den synlige projektkreditering er bevaret. Appens kode følger manifestets
`GPL-3.0-or-later`; der er ingen nye medfølgende tredjepartsbiblioteker.

## Lokal kontrol

Kør fra produktroden:

```powershell
node --test apps/lommeregner/tests/calculator.test.mjs
python -B sdk/check_app.py --app lommeregner
python -B sdk/preview.py --app lommeregner
```

Åbn den udskrevne loopback-adresse. Preview er en frossen kopi: stop med
Ctrl+C og start igen efter kildeændringer. Stop også efter afprøvning, så
værten og de midlertidige filer ryddes.

Mens preview kører, kan projektets eksisterende Playwright/Edge og axe
bruges til et lille renderet flowtjek:

```powershell
node apps/lommeregner/tests/browser-smoke.mjs
```

Regnetestene dækker regnearter, decimaltal/afrunding, kædning, skift af
regneart, nuldivision og rettelse, fortegn før indtastning/efter resultat,
sletning, rydning og talgrænser. Browserprøven dækker klik på decimaltal,
nuldivision, rettelse og tastatur samt axe i normal- og fejltilstand.

Den 15. november 2026 bestod 6 regnetests og SDK-kontrollen (5 pakkefiler).
Browserflowet bestod med 0 JavaScript-fejl og 0 automatiske axe-violations;
`color-contrast` og `label-content-name-mismatch` har manuelle/incomplete
fund, som skal vurderes i UI-reviewet. Renderprøver ved 1024 × 688 og
1024 × 768 viste alle regnetaster. Hjælp/kreditering kan kræve lodret scroll.
Reflow ved 512 × 344 (svarende til 200 % af 1024 × 688) havde ingen vandret
scroll og bevarede klikmål over 44 × 44 CSS-pixel. Det er ekspert-/funktions-
kontrol, ikke en elevbrugerprøve eller fuld tilgængelighedscertificering.

Guidens SDK-test (8), katalogtest (21), pakkeejerskabstest (8) og
`python -B scripts/public_source.py --check` blev også kørt. Katalogtestens
oprindelige selvstændige kommando manglede `system` på Python-stien; den
fælles testindgang blev rettet uden for appen, og guidekommandoen består nu.

## Pakning og installation

`files.json` pakker kun `web/`, under
`usr/share/makervaerkstedet/apps/lommeregner/www/`. Tests og denne README
indgår ikke i appens programfiler. Se `docs/NY-APP-GUIDE.md` for separat
Debian-pakning, signeret katalog og lærerens installation. Lokal preview
beviser ikke elevbjælke, kold/offline opstart, adgangsstyring eller en
installeret udgivelse. Disse prøver hører til integrationsafleveringen.

## Erfaring med appguiden

Skabelonen gav korrekte manifestejerskaber og en straks brugbar lokal vært,
font og temabro uden npm-installation eller ændringer i systemkode. Guiden
var tilstrækkelig til at implementere appen og nå frem til lokal validering.
Det eneste konkrete tekniske hul var katalogtestens Python-importindgang,
som beskrevet ovenfor. Den installerede Python var 3.14.4 (guiden omtaler
3.13); de brugte værktøjer fungerede på 3.14.4.

Lommeregnerens talgrænse, afrunding og rækkefølge er bevidste enkle appvalg,
ikke noget SDK'et bestemmer. Preview kopierer filerne ved start, hvilket
krævede genstart efter reviewrettelser. Signeret katalog, pakning og reel
VM-testinstallation håndteres separat; appen er ikke publiceret herfra.

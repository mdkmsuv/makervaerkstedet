# Serverkontrolpanel

Det loginbeskyttede kontrolpanel ligger på `/kontrolpanel` (samme lærerpanel
kan også åbnes på `/`). Det viser den aktuelle serverversion, tællinger,
printerstatus og elevcomputere samlet øverst i lærerfladen.

Efter en ny Windows-installation ligger genvejen **Elevhub kontrolpanel** på
serverens skrivebord. Skoleadmin, menupunkt 8, åbner samme side på
`https://127.0.0.1:8780/kontrolpanel`.

Panelet henter status med lærerens eksisterende cookie-login fra
`GET /api/laerer/system`. Det opdaterer ved åbning og derefter cirka hvert 17.
sekund, så længe læreren er logget ind. **Opdatér overblik** kan bruges til en
manuel hentning; forespørgsler overlapper ikke. Ved logout stoppes polling, og
sene svar kasseres.

For hver elevcomputer vises navn, IP, online/offline, seneste kontakt,
tilmelding, regelbekræftelse og den eksplicitte softwareversion. Den interne
policy-version bruges ikke som softwareversion. `current`, `different` og
`unknown` vises hver for sig, og ukendt eller offline status bliver ikke
præsenteret som opdateret.

Printerlisten viser kun aktuelle data, når serveren melder `status: ok`.
Ved ukendt printerstatus vises ingen gamle eller antagede printere. Printere
tilføjes og redigeres på printer-/lærersiden; kontrolpanelet duplikerer ikke
LAN-koder eller printeropsætning.

## Genstart hele Windows-serveren

Under serveroplysningerne kan en indlogget lærer vælge **Genstart server**.
Handlingen genstarter hele Windows-computeren eller serverens VM. Alle
serverens tjenester bliver afbrudt, herunder lærerfladen, Printværkstedet og
Designværkstedet. Gem igangværende arbejde, og sørg for, at maskinerne kan
undvære serveren under genstarten. På en fælles Windows-computer berøres også
andre åbne programmer. På en VM genstartes Windows inde i VM'en.

Panelet kræver en særskilt bekræftelse: skriv **GENSTART HELE SERVEREN** og
vælg **Bekræft genstart af hele serveren**. Annullér lukker bekræftelsen uden
at bestille noget. En modtaget bestilling er endnu ikke bevis for en
gennemført genstart. Vent, og åbn panelet igen, når serveren svarer.

Hvis forbindelsen forsvinder før svaret, vises resultatet som **ukendt**.
Genstarten kan allerede være bestilt. Panelet gentager aldrig bestillingen
automatisk og låser knappen efter et forsøg. Kontrollér serveren lokalt,
før du eventuelt logger ind igen og bestiller på ny.

Når websiden ikke svarer, bruges **Genstart Makerværkstedets server** på
selve Windows-serverens skrivebord. For en VM åbnes genvejen inde i VM'en.
Genvejen fungerer uafhængigt af webtjenesten og kræver lokal bekræftelse.
Hvis genvejen mangler, skal skolens IT kontrollere installationen.

Webknappen er kun tilgængelig på Windows med den installerede,
adgangsbeskyttede genstarthjælper. På Linux, ved manglende hjælper eller ved
statusfejl er den deaktiveret. **Opdatér overblik** kontrollerer status igen.
En kildeændring alene installerer ikke hjælperen på skolens server.

De to lærerendepunkter er `GET /api/laerer/server-restart` (tilgængelighed)
og `POST /api/laerer/server-restart` (bestilling). Begge kræver en gyldig
lærersession; elev- og tilmeldingstokens giver ikke adgang. POST kræver
panelets egen Origin, JSON og præcis
`{"confirmation":"GENSTART HELE SERVEREN"}` uden ekstra felter. Kun et
udtrykkeligt accepteret svar fra hjælperen giver HTTP 202 med
`{"accepted":true}`. Et mislykket eller ubekræftet svar giver HTTP 503 med
`{"accepted":false}`; dette dokumenterer ikke, om Windows allerede har
modtaget en genstart.

Browseren kan hverken vælge kommando, filsti eller operativsystemargumenter.
Serveren kalder kun Windows' PowerShell og den faste installerede
`Program Files\ElevhubServer\server\server-restart.ps1` med `-Mode Status`
eller `-Mode Request`. Kaldet har tidsgrænse, og private diagnosticeringsdata
vises ikke i browseren.

Genstartfunktionen starter ikke elevcomputere, ændrer printeropsætning eller
installerer opdateringer.

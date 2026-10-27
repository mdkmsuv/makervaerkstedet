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

Kontrolpanelet er et statusoverblik. Det starter ikke elevcomputere, ændrer
printere og installerer ikke opdateringer.

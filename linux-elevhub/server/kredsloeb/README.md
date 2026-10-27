# Kredsløbsværksted – testprototype

Dette er en lokal dansk elevindgang til [CircuitJS1](https://github.com/pfalstad/circuitjs1). Simulatoren er et eksisterende open source-værktøj; elevindgangen og de seks opgaver er det lokale lag. Alle runtime-filer ligger i `vendor/circuitjs1`, så serveren kan bruges uden internet efter installation. Projektdata sendes ikke til en ekstern tjeneste.

## Start på udviklingscomputeren

Fra projektroden:

```powershell
powershell -ExecutionPolicy Bypass -File .\linux-elevhub\server\kredsloeb\start-preview.ps1 -Open
powershell -ExecutionPolicy Bypass -File .\linux-elevhub\server\kredsloeb\test-preview.ps1
node .\linux-elevhub\server\kredsloeb\test-browser.cjs
```

Åbn [http://127.0.0.1:8812/](http://127.0.0.1:8812/). Serveren binder kun til loopback i previewet. Stop med:

```powershell
powershell -ExecutionPolicy Bypass -File .\linux-elevhub\server\kredsloeb\stop-preview.ps1
```

I Elevhub køres værkstedet som den lokale kredsløbsapp på `127.0.0.1:8794`. Elevhub injicerer projektbroen fra `/_elevhub/projekter/bridge.js`, og adapteren i `app.js` giver broen en komplet, redigerbar CircuitJS1-tekstfil. Browseren og projektdata bliver på elevcomputeren; der bruges ingen central server eller LAN-adresse til elevens kredsløb. Preview-scriptet på port 8812 er kun til udvikling.

## Forslag til første test

Lad en elev vælge `1 · Kontakt og lampe`. Skriv en forudsigelse, før kontakten lukkes, og følg strømmen i lampen. Fortsæt derefter med `2 · Ohms lov`, seriekoblingen og parallelkoblingen. Brug `File → Export as Text` i simulatoren og gem filen i elevens projektmappe; den kan hentes med `File → Import from Text`.

Opgaverne bruger kun ideelle lavspændingsmodeller i simulatoren. De er en faglig simulering og en forberedelse til et fysisk kredsløb, ikke en byggevejledning til lysnettet. Demoen har en lokal Content-Security-Policy, der kun tillader samme-origin-ressourcer (`connect-src 'self'`). CircuitJS1's eventuelle Dropbox-/kortlink-funktioner er ikke forbundet; projektbroen bruger kun Elevhubens lokale same-origin API.

## Open source-proveniens

Den vendorerede CircuitJS1-build blev hentet fra den officielle upstream GitHub Pages-build på `https://pfalstad.github.io/circuitjs1/circuitjs.html` den 19. september 2026. Den tilhørende kildekode er pinned til upstream-commit `5bdb1296ce6a82f79515f4f1dd1b9a86e03236f7` i [pfalstad/circuitjs1](https://github.com/pfalstad/circuitjs1/tree/5bdb1296ce6a82f79515f4f1dd1b9a86e03236f7). CircuitJS1 er GPL version 2 eller senere; den fulde licens ligger i [`vendor/circuitjs1/COPYING.txt`](vendor/circuitjs1/COPYING.txt). LZString er krediteret af upstream og ligger med i builden.

Builden er ikke kørt fra en public CDN i elevens browser. Wrapperen bruger kun relative lokale asset-stier. `vendor/circuitjs1/circuitjs.html` er justeret til at fjerne upstreams service-worker-registrering, fordi værkstedet serveres fra Elevhubben; simulatorens compiled GWT-code er ellers uændret. Den tilsvarende upstream-kilde er gemt i `vendor/circuitjs1/circuitjs1-source-5bdb1296ce6a82f79515f4f1dd1b9a86e03236f7.zip`, og alle vendorerede runtime-filer samt den lokale font er listet i `ASSET-SHA256.txt`.

`test-browser.cjs` kræver Playwright i udviklingsmiljøet, åbner kun loopback, vælger alle seks opgaver, kontrollerer komponenttyper og målinger, afviser eksterne requests og skriver `test-browser-output.png` (eller stien i `KREDSLOEB_SCREENSHOT`). Native CircuitJS-eksempler ligger i `vendor/circuitjs1/circuitjs1/circuits`, fordi det er den sti, som den vendorerede build requests.

## Begrænsninger i prototypen

- Simulatorens menu- og komponenttekster bruger den medfølgende danske `locale_da.txt`; den danske indgang, opgaver og arbejdsmetode er også på dansk.
- Elevprojekt-gemning bruger CircuitJS1's native tekst-eksport via `window.ElevhubProjectAdapter`; Elevhubens lokale projektbro viser Gem/Åbn, når appen startes gennem Elevhub.
- CircuitJS1 viser skemaer, komponentværdier og målinger. Den viser ikke en fysisk breadboard-opstilling.
- LAN-adgang og Elevhub-registrering er beskrevet, men denne prototype åbner ikke selv firewallregler eller serviceinstallation.

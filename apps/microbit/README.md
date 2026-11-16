# micro:bit

En lille lokal ramme om den officielle MakeCode-editor. Eleven programmerer
i MakeCode; rammen gemmer og genåbner projektet på elevcomputeren. App-id
`microbit`, loopback-port 8797, projektformat `.microbit.json`.

## Sådan hænger det sammen

`web/index.html` viser `https://makecode.microbit.org/?lang=da&controller=1`
i en iframe. `web/app.js` taler MakeCodes `pxteditor`/`pxthost`-protokol over
`postMessage`, accepterer kun beskeder fra netop den origin og den iframe, og
stiller `window.ElevhubProjectAdapter` til rådighed for den fælles projektbro.
Projektfilen er JSON med `format: "elevhub-microbit-project"` og `version: 1`
omkring MakeCodes eget projekt.

Selve editoren følger **ikke** med appen. Manifestet siger derfor:

| Felt | Værdi | Betydning |
|---|---|---|
| `kraever_internet` | `true` | Editoren hentes fra MakeCode og virker ikke uden internet |
| `offline` | `true` | Flisen forbliver åben, når skoleserveren ikke kan nås; appen afhænger ikke af den |

Svarer editoren ikke inden for 8 sekunder ved gem eller åbn, får eleven en
dansk besked om, at micro:bit kræver internet. Overførsel til en fysisk
micro:bit sker i MakeCode gennem browserens USB-adgang (`allow="usb"`).

Der er intet byggetrin og ingen kø. `files.json` pakker **hele** `web/` som
træ, så alt i mappen følger med ud på elevcomputeren.

## Prøv ændringer

Kør fra produktroden:

```powershell
python -B sdk/check_app.py --app microbit
python -B sdk/preview.py --app microbit
node system/linux-elevhub/tests/test_microbit_offline_message.cjs
```

`system/linux-elevhub/tests/integration_makecode_browser.js` er en bevidst
tilvalgt prøve mod den rigtige MakeCode-tjeneste. Den kræver internet og
Playwright og bruger hverken login, USB eller en fysisk enhed.

## Kendt oprydning

`web/test-protocol.cjs` er en ældre protokolprøve, som ligger i den pakkede
mappe og derfor følger med ud til eleverne. Den læser desuden appen på en sti
fra før appopdelingen og fejler i den aktuelle kilde. Flyt den til
`apps/microbit/tests/`, ret stien, og bump appens version ved samme lejlighed;
pakkens indhold ændres, så det kan ikke ske under version `1.0.0`.

[Opdatér, byg og udgiv appen](../../docs/NY-APP-GUIDE.md)

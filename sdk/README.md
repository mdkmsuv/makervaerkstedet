# App-SDK

Udviklingsværktøjer til en selvstændig app under `apps/<id>/`.
Kør fra produktroden med `REPOSITORY-LAYOUT.json`:

```powershell
python -B sdk/new_app.py --id plantequiz --name "Plantequiz" --port 8890 --stateless
python -B sdk/check_app.py --app plantequiz
python -B sdk/preview.py --app plantequiz
```

Brug `--project` i stedet for `--stateless` for et færdigt eksempel med
typet gem, genåbn og elevskift. Opretteren skriver kun en ny appmappe.
Den lokale prøve bruger midlertidige data og den rigtige appvært, tema-
og projektbro; den installerer intet og starter ingen køhandling.
Afslut med Ctrl+C.

Læs [hele ny-app-guiden](../docs/NY-APP-GUIDE.md) for manifest,
capabilities, adapter, valgfri aflevering, kontrol, apppakke og signeret
distribution. [Appplatformen](../docs/APP-PLATFORM.md) forklarer
ejerskab, katalog og forskellen på installeret og åben i undervisningen.

- `templates/`: de færdige statiske varianter.
- `examples/queue.mjs`: valgfri køintegration uden handling ved import.
- `web/`: fælles browserkontrakter.
- `check_app.py`: validerer med systemets faktiske pakkekontrakt.

Aktuel SDK er 1, mindste kerne for nye apps er `2.1.0~`. Appkode,
appversion og artefakt ejes af appen; en ny app kræver ingen ændring i
hovedsystemets version. Skoleprofiler, nøgler og elevdata må ikke pakkes.
Bevar [kreditering](../CREDITS.md) og
[licensafgrænsning](../LICENSE-SCOPE.md).

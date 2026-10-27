# Elevhub: elevkomponent i Makerværkstedet

**Kreditering: Klostermarksskolen.**

Makerværkstedet er lærerens samlede styring af makerspace. Elevhub forbinder
Linux-elevcomputere og digitale værksteder med lærerens kontrolpanel.
Maskiner, workflows og administration virker også med nul elevcomputere.
Det aktuelle mål er modning i kildekode og tests; nye installationspakker
og ISO’er afventer den samlede verifikation.

Læs [hovedvejledningen](../README.md), [arkitekturen](../docs/MAKERSPACE-ARKITEKTUR.md),
[skoletilpasning](../docs/SKOLETILPASNING.md) og den lokale installationsvejledning.
[Kodeagentens indgang](../docs/AGENTINDGANG.md) består af Markdown; læreren
styrer den daglige drift gennem kontrolpanelet.

## Komponenter og data

| Kilde | Ansvar |
|---|---|
| [server/elevhub](server/elevhub) | Lærerlogin, elevstatus, åbne værksteder, maskinadministration og workflows. |
| [ansible](ansible/README.md) | Linux-kiosk, loopback-proxy, HTTPS-tillid og enhedsopsætning. |
| [projekter](projekter) | Elevens lokale projektarkiv og fælles projektvælger. |
| [cadoodle](cadoodle/README.md) | Native 3D-program og vores tilpasningskilder. |
| [installation](installation/README.md) | Kilder til elevinstallation og opdatering. |
| [backup](backup/README.md) | Valgfri lokal versionsbackup til et eksplicit valgt mål. |

### Skoleprofil i den native 3D-indgang

Ansible skriver den valgte `school_name` til elevcomputerens lokale `kiosk.json`.
Den native CaDoodle-indgang bruger navnet i sine Tk-vinduestitler og øverste
overskrifter, med `Makerværkstedet` som fallback ved en manglende eller ugyldig
værdi. Inden visning fjernes kontrol-/formattegn, linjeskift samles til enkelt
mellemrum, og navnet begrænses til 80 tegn. Det ændrer kun den lokale visning;
det giver ikke skolen en ny adgangs- eller netværksrolle.

Krediteringen vises separat som **Kreditering: Klostermarksskolen.** og kan
derfor ikke overskrives af den valgte skoles navn eller tema. Den samme
kreditering gælder kildekoden og de øvrige udgivelsesdokumenter.

Eleverne bygger lokalt. Afleveringer til læreren går til serverens
printerkø eller laser-Inbox. Elevcomputeren ejer sine projektfiler;
serveren ejer afleveringernes status og lærerens valg. Personlige
skoleprofiler, nøgler, certifikater og elevdata er ikke byggekilder.

Ved utilgængelig server er alle værksteder, CaDoodle og Internet åbne,
også ved kold opstart. Gamle lukninger og skærmpauser må ikke fastholdes.
Ved genforbindelse anvendes lærerens aktuelle valg igen. Dette er en fast
produktkontrakt og erstatter ældre pilotnoter om lukning ved tabt kontakt.

Chromium bruger en fast loopback-proxy til Internet. Skoleserveren og
loopback-værkstederne undtages med en tekststreng i `ProxyBypassList`.
Installation og opdatering kontrollerer formatet; kioskopstart logger
konfigurationsfejl uden at gøre offline værkstedsadgang afhængig af serveren.

## Verifikation

Kør fra projektroden efter installation af runtime- og testafhængigheder:

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -B scripts/run_tests.py
node linux-elevhub/tests/test_kiosk_offline_reconnect.cjs
```

Runneren viser platformafhængige skips. Linux-installation, systemd,
netfilter og permissionsprøver skal også køres på Linux/WSL; en skip er
ikke en bestået test. Skolens hardwareprøver skal dokumenteres særskilt. Historiske rapporter må ikke bruges som status for en
ny installation.

Elevhubs egne eksisterende dele beholder [MIT-licensen](LICENSE).
[Tredjepartsnotices](THIRD_PARTY.md) og [licensafgrænsningen](../LICENSE-SCOPE.md)
beskriver de øvrige komponenter. Krediteringen af Klostermarksskolen bevares,
når en anden skole vælger navn og tema.

# Elevhub Windows server package

> Offentlig dokumentation: LOKAL_IP er en pladsholder. Brug skolens egne adresser.

This directory is the source contract for the Windows 10/11 x64 server package.
It is intentionally separate from the existing Printvaerksted installation.
The eventual package has this layout:

The source also has an opt-in `-GenerationLayout` first-install path for
development. It keeps immutable sources in `program/` and uses one selected
program/data pair through the fixed interpreter. This is not in a released
package; update activation, rollback, generation removal and a full Windows
installation test are still pending. See
[the maturation status](../../docs/MAKERSPACE-OPDATERING.md). The default
layout below remains unchanged.

```text
Program Files/ElevhubServer/
  runtime/python.exe
  app/                 # Printvaerksted and laser backends
  web/
  hub/                 # linux-elevhub/server/elevhub
  server/              # bootstrap, supervisor and provisioning
ProgramData/ElevhubServer/
  data/ logs/ security/
```

The supervisor owns three processes: Printvaerksted on `127.0.0.1:8765`,
the laser/folie backend on `127.0.0.1:8766` and teacher port `8767`, and
Elevhub on `LOKAL_IP:8770-8773` with teacher panel `LOKAL_IP:8780`.
New installations require an explicit `-RoleIp`. The package template uses a
reserved documentation address, which is not a selected installation address.
The installer rewrites the runtime
configuration and certificate SAN for that address.  Local backend ports are
also configurable with `-PrintBackendPort`, `-DesignBackendPort` and
`-TeacherBackendPort` so a staged instance can coexist with existing local
services.  The exact production TLS configuration is supplied separately; this source
does not create or copy secrets during a build.

`bootstrap.py --check` and `verify.py` are read-only checks. The PowerShell
installer refuses to overwrite existing state, validates the configured role IP,
uses separate ACL-protected data, and writes an installation manifest only after
all checks pass. A later native service wrapper may call `bootstrap.py`; the
protected Scheduled Task fallback is provided by `install.ps1`.

The server package does not contain pupil-local Scratch, Datatraeneren or
Kredsloeb services. Those remain part of the pupil client package. It also does
not include printer credentials. Enrollment is an explicit administrator action:
`provision.py enroll` adds a compatible entry to an administrator-selected
`enheder.json` without modifying a host installation by default.

Provision private runtime material outside the source tree before installation:

```powershell
runtime\python.exe server\provision.py init-tls --output-dir .\security --server-ip LOKAL_IP
runtime\python.exe server\provision.py enroll --registry .\security\enheder.json --name elev-01 --server https://LOKAL_IP:8773 `
  --ca-certificate .\security\ca.crt --output .\security\elev-01.json --write
```

The enrollment file contains `schemaVersion: 1`, the server URL, both clear
tokens and the CA PEM for the selected pupil. Keep it out of source control.

## Verification

```powershell
python server\verify.py --package-root . --role-ip LOKAL_IP
python server\bootstrap.py --package-root . --data-root .\fixture-data --role-ip LOKAL_IP --check
.\install.ps1 -CheckOnly -PackageRoot . -RoleIp LOKAL_IP

# Temporary workstation example (use an elevated PowerShell for install):
.\install.ps1 -PackageRoot . -RoleIp LOKAL_IP `
  -TeacherAddress 127.0.0.1 -TeacherPort 8781 `
  -PrintBackendPort 18765 -DesignBackendPort 18766 -TeacherBackendPort 18767
```

The tests use temporary fixtures and never register tasks, change firewall rules,
write certificates, or touch `Program Files`/`ProgramData`.

## Adding Elevhub alongside the school's existing Printvaerksted

On SERVERNAVN (`LOKAL_IP`), first inspect installed services, existing Elevhub
security/configuration, and ownership of ports 8766/8767. The current Printvaerksted
queue is an independent installation and must not be replaced or restarted.

For a verified host with no existing Elevhub installation or design backend:

```powershell
.\install.ps1 -PackageRoot . -RoleIp LOKAL_IP `
  -TeacherAddress 127.0.0.1 -UseExistingPrintBackend
```

The explicit switch checks `http://127.0.0.1:8765/api/health` before changes and
requires a non-demo Printvaerksted service. Runtime configuration records
`external_backends: ["print"]`; the supervisor starts only design and the HTTPS
gateway. It never starts, stops or owns the external print process. Installation
and rollback leave its task, data, printer settings and queue untouched. The
teacher panel stays on loopback, accessible locally or through an SSH tunnel.

Runtime configuration can also declare `design` as external when another
verified installation owns it. The installer switch above only supports an
existing print backend; it deliberately refuses occupied design ports.

This option has code and local HTTP fixture tests. It has not yet been deployed
or verified on the school VM; a reachable print health page does not establish
SSH access or prove that the HTTPS gateway is installed.

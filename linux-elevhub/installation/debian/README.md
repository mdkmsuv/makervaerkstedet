# Generic Debian student installer

`build_iso.py` builds a public Debian13.7 netinstall image with the four exact
packages accepted by `packaging/build_release.py`. Required inputs are a verified
base ISO, the signed flat project repository, the four-package simulation receipt
and its SHA256. The builder uses existing Python3, xorriso, cpio, gzip, OpenSSL,
dpkg/apt and the existing BIOS/UEFI ISO builder plus disk-selection guard. It
installs no tools, packages or services on the build machine. Native APT checks
use empty-status, hook-free temporary roots and retain native signatures, HTTPS
and Debian Valid-Until verification.

The ISO contains program files, public signing keys and installer sources only.
No device profile, server address, CA/private key, ticket, password, Wi-Fi secret
or student data is embedded. Debian mirror access is required during installation
for normal OS/runtime dependencies. This is a netinstaller, not a fully offline
live system. The extra existing enrollment dependencies are openssh-client,
libnss3-tools and ca-certificates; console/keyboard setup is also explicit. No SSH
server is added. The recipe dependency gate simulates all four program packages
and these OS packages together before image construction.

The existing disk guard selects one eligible internal disk, excludes installation
USB/ambiguous targets and requires explicit confirmation of disk erasure. The
initial preseed disk is deliberately invalid until the guard succeeds. This is
CLEAN INSTALLATION ONLY: it erases the selected disk, and must never be used for
ordinary program updates. Normal updates use apt and preserve identity and data.

On first boot, local workshops are rendered offline even without school setup.
A short optional console prompt offers the EXISTING school-connection.json USB
and single-use enrollment ticket. Enter/wait skips school contact and starts the
local workshops. Reboot to choose setup later. The operator explicitly selects
the file, computer name and ticket and confirms JA. Enrollment uses existing TLS
port8774; the new helper reuses the existing profile validator without executing
the historical Ansible firstboot program. A pure validator reuses the unchanged function text (normalized LF), without its historical deployment entrypoint.

The root device token is stored0600 outside packages. Only the UI token enters
kiosk configuration/templates. School CA trust uses the same system/Chromium NSS
method as the existing kiosk role. Public project signing keys bootstrap the apt
client; no updater configuration is created until enrollment is available. The
teacher then binds the registered computer to student/debian-amd64 and chooses an
update. No role is inferred from the enrollment client. This introduces no pupil
login or storage scheme. Projects remain /home/elev/Projekter.

`install_payload.py --payload <path>` only checks payload hashes. Its explicit
`--apply` option is invoked by Debian Installer on the freshly partitioned target,
never by the image builder/tests. It installs signed exact package versions,
creates the local kiosk session and boot links, and does not start services in
the installer. No destructive purge or migration of existing data is provided.
The generic local OS account has a locked password; this recipe adds no remote
administration service or replacement admin/login model.

Build evidence records hashes, package versions, native dependency simulation,
BIOS/UEFI records, initrd/preseed/diskguard and read-back payload checks. Those
checks are not evidence of boot, successful installation, graphical session,
USB/audio/display operation or powercut recovery. Those require a separately
authorized installation/pilot trial. Neither building nor extracting this image
boots it, writes a disk, contacts a school, or enables host services.

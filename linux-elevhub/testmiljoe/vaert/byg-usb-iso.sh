#!/bin/bash
# Byg en selvstaendig, bootbar Debian-ISO med svarfilen bagt ind.
#
# Udfyld foerst svarfilen med lav-usb-stik.ps1 paa Windows.
# Koer derefter dette script i Linux/WSL med argumenterne nedenfor.
# Kraever xorriso og cpio; scriptet installerer ikke vaerktoejerne.
#
# Resultatet er EN ISO-fil, som skrives til EN USB-noegle med Rufus i
# DD-tilstand. Ingen ekstra noegle, ingen tastetryk ved opstart.
#
# Argumenter:
#   $1  kilde-ISO (Debians officielle netinst), fx /mnt/d/Elevhub-VM/iso/debian-13.7.0-amd64-netinst.iso
#   $2  faerdig preseed.cfg (allerede udfyldt af lav-usb-stik.ps1)
#   $3  sti til den ISO, der skal laves
#   $4  computernavn, kun til menuteksten
#   $5  valgfri elevhub-mappe med fuld softwarepakke og enhedsprofil

set -euo pipefail

KILDE_ISO="$1"
PRESEED="$2"
UD_ISO="$3"
NAVN="${4:-elevhub}"
ELEVHUB_MAPPE="${5:-}"

# Full payloads must fit on disk; WSL /tmp can be a small RAM-backed filesystem.
ARBEJDE="$(mktemp -d "${TMPDIR:-/tmp}/elevhub-iso-XXXXXX")"
trap 'rm -rf "$ARBEJDE"' EXIT

echo "== 1/5 Pakker kilde-ISO ud =="
mkdir -p "$ARBEJDE/iso"
xorriso -osirrox on -indev "$KILDE_ISO" -extract / "$ARBEJDE/iso" 2>&1 | tail -2
chmod -R u+w "$ARBEJDE/iso"
if [ -n "$ELEVHUB_MAPPE" ]; then
    test -f "$ELEVHUB_MAPPE/payload.tar.gz"
    # Personal/shared media carry profiles; public generic media carries the
    # onboarding guide instead. Both forms still require the software payload.
    test -f "$ELEVHUB_MAPPE/profile/device.json" || test -f "$ELEVHUB_MAPPE/profiles/names.txt" || test -f "$ELEVHUB_MAPPE/onboarding.py"
    mkdir -p "$ARBEJDE/iso/elevhub"
    cp -a "$ELEVHUB_MAPPE/." "$ARBEJDE/iso/elevhub/"
fi

echo "== 2/5 Laegger svarfilen ind i installerens initrd =="
# Debians officielle metode til initrd-preseeding: pak ud, tilfoej preseed.cfg
# som cpio-post, pak sammen igen. Installationsprogrammet laeser /preseed.cfg
# fra sin egen initrd helt automatisk, uden boot-parametre.
mkdir -p "$ARBEJDE/ps"
cp "$PRESEED" "$ARBEJDE/ps/preseed.cfg"
if [ -n "$ELEVHUB_MAPPE" ]; then
    # A direct partman init hook can abort safely; preseed_command ignores a
    # failing command after showing its error. Install the hook before boot,
    # so it is already in partman's init.d glob on the very first invocation.
    # Debian 13's initrd has /lib -> usr/lib. Preserve that symlink by adding
    # files at the canonical usr/lib path, never a replacement lib directory.
    mkdir -p "$ARBEJDE/ps/usr/lib/partman/init.d" "$ARBEJDE/ps/usr/share/elevhub"
    cp "$ELEVHUB_MAPPE/partman-disk-guard.sh" "$ARBEJDE/ps/usr/lib/partman/init.d/00elevhub-disk-selection"
    cp "$ELEVHUB_MAPPE/disk-selection.templates" "$ARBEJDE/ps/usr/share/elevhub/"
    chmod 755 "$ARBEJDE/ps/usr/lib/partman/init.d/00elevhub-disk-selection"
    chmod 644 "$ARBEJDE/ps/usr/share/elevhub/disk-selection.templates"
    if [ -f "$ELEVHUB_MAPPE/profiles/names.txt" ]; then
        # Explicit identity selection runs before the disk guard and all of
        # Debian's partition initialization. Back/cancel must abort partman.
        cp "$ELEVHUB_MAPPE/select-computer.sh" "$ARBEJDE/ps/usr/lib/partman/init.d/00elevhub-computer-selection"
        cp "$ELEVHUB_MAPPE/computer-selection.templates" "$ARBEJDE/ps/usr/share/elevhub/"
        chmod 755 "$ARBEJDE/ps/usr/lib/partman/init.d/00elevhub-computer-selection"
        chmod 644 "$ARBEJDE/ps/usr/share/elevhub/computer-selection.templates"
    fi
fi
gunzip -c "$ARBEJDE/iso/install.amd/initrd.gz" > "$ARBEJDE/initrd"
( cd "$ARBEJDE/ps" && find . -mindepth 1 -printf '%P\n' | cpio -H newc -o -A --owner=0:0 -F "$ARBEJDE/initrd" 2>/dev/null )
gzip -9 -c "$ARBEJDE/initrd" > "$ARBEJDE/iso/install.amd/initrd.gz"
echo "   initrd: $(stat -c%s "$ARBEJDE/iso/install.amd/initrd.gz") byte"

echo "== 3/5 Saetter automatisk opstart (BIOS + UEFI) =="
# BIOS (isolinux): boot vores post efter 5 sekunder uden menu.
# Wi-Fi's netvaerksnavn, sikkerhedstype og adgangsfrase har prioritet high.
# critical skjuler kodefeltet og giver en endeloes fejl om en tom WPA-kode.
# Svarfilen holder de allerede udfyldte installationsvalg automatiske.
cat > "$ARBEJDE/iso/isolinux/elevhub.cfg" <<EOF
label elevhub
	menu label ^ELEVHUB automatisk installation
	kernel /install.amd/vmlinuz
	append initrd=/install.amd/initrd.gz auto=true priority=high vga=788 --- quiet
EOF
cat > "$ARBEJDE/iso/isolinux/isolinux.cfg" <<'EOF'
# ELEVHUB: automatisk installation. Den oprindelige menu er erstattet.
path
prompt 0
timeout 50
default elevhub
include elevhub.cfg
include menu.cfg
EOF

# UEFI (grub): vores post indsaettes foerst og vaelges som standard.
python3 - "$ARBEJDE/iso/boot/grub/grub.cfg" "$NAVN" <<'PYEOF'
import sys
sti, navn = sys.argv[1], sys.argv[2]
with open(sti, encoding='utf-8') as f:
    tekst = f.read()
post = (
    "set default=0\n"
    "set timeout=5\n"
    "menuentry 'ELEVHUB automatisk installation (%s) - SLETTER HELE DISKEN' {\n"
    "    set background_color=black\n"
    "    linux    /install.amd/vmlinuz auto=true priority=high vga=788 --- quiet\n"
    "    initrd   /install.amd/initrd.gz\n"
    "}\n" % navn
)
maal = "menuentry --hotkey=g 'Graphical install' {"
if maal not in tekst:
    sys.exit("Fandt ikke den forventede grub-menupost. ISO-udgaven er maaske aendret.")
tekst = tekst.replace(maal, post + maal, 1)
with open(sti, 'w', encoding='utf-8') as f:
    f.write(tekst)
PYEOF

echo "== 4/5 Opdaterer md5sum.txt =="
# Ellers fejler "Check disc integrity", fordi vi har aendret filer.
( cd "$ARBEJDE/iso" && rm -f md5sum.txt && \
  find . -type f ! -name md5sum.txt -print0 | xargs -0 md5sum > md5sum.txt )

echo "== 5/5 Bygger hybrid BIOS+UEFI-ISO =="
# Optionerne er hentet fra kilde-ISO'en med:
#   xorriso -indev <iso> -report_el_torito as_mkisofs
mkdir -p "$(dirname "$UD_ISO")"
xorriso -as mkisofs \
    -iso-level 3 \
    -V 'ELEVHUB DEBIAN13' \
    -o "$UD_ISO" \
    -isohybrid-mbr --interval:local_fs:0s-15s:zero_mbrpt,zero_gpt,zero_apm:"$KILDE_ISO" \
    -partition_cyl_align on \
    -partition_offset 0 \
    -partition_hd_cyl 64 \
    -partition_sec_hd 32 \
    --mbr-force-bootable \
    -apm-block-size 2048 \
    -iso_mbr_part_type 0x00 \
    -c '/isolinux/boot.cat' \
    -b '/isolinux/isolinux.bin' \
    -no-emul-boot \
    -boot-load-size 4 \
    -boot-info-table \
    -eltorito-alt-boot \
    -e '/boot/grub/efi.img' \
    -no-emul-boot \
    -boot-load-size 7360 \
    -isohybrid-gpt-basdat \
    -isohybrid-apm-hfsplus \
    -r -J -joliet-long \
    "$ARBEJDE/iso" 2>&1 | tail -5

echo ""
echo "FAERDIG: $UD_ISO"
ls -lh "$UD_ISO"

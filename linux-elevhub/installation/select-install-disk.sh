#!/bin/sh
# Select exactly one safe Debian installer target.
#
# Production reads only /sys/block and /proc/mounts.  The ELEVHUB_TEST_* knobs
# exist solely for the shell fixtures and are never set by the generated ISO.
set -eu

sysroot=${ELEVHUB_TEST_SYS_ROOT:-/sys}
mounts=${ELEVHUB_TEST_MOUNTS:-/proc/mounts}
test_mode=${ELEVHUB_TEST_MODE:-0}
boot_source=''
count=''

# One photo of the error screen must be enough to diagnose an unknown PC:
# advice first, then every disk and every PCI storage controller.
inventory() {
    hint=''
    disks=''
    for entry in "$sysroot"/block/*; do
        [ -d "$entry" ] || continue
        name=${entry##*/}
        case "$name" in loop*|ram*|zram*|fd*) continue ;; esac
        sectors=$(cat "$entry/size" 2>/dev/null || echo 0)
        case "$sectors" in ''|*[!0-9]*) sectors=0 ;; esac
        removable=$(cat "$entry/removable" 2>/dev/null || echo '?')
        ro_flag=$(cat "$entry/ro" 2>/dev/null || echo '?')
        case "$(readlink -f "$entry" 2>/dev/null)" in
            */usb[0-9]*) bus=usb ;;
            *) bus=intern ;;
        esac
        model=$(cat "$entry/device/model" "$entry/device/name" 2>/dev/null | head -n 1 | tr -s ' ' | cut -c1-24)
        disks="$disks $name=$((sectors / 2097152))GB,$bus,rm$removable,ro$ro_flag${model:+,$model};"
    done
    controllers=''
    for device in "$sysroot"/bus/pci/devices/*; do
        [ -r "$device/class" ] || continue
        class=$(cat "$device/class")
        case "$class" in 0x01*) ;; *) continue ;; esac
        vendor=$(cat "$device/vendor" 2>/dev/null || true)
        product=$(cat "$device/device" 2>/dev/null || true)
        controllers="$controllers ${vendor#0x}:${product#0x}/${class#0x}"
        # PCI class 0x0104 is a RAID controller: in Intel RST mode the SATA
        # disks are hidden from Linux.  The IDs below are Intel VMD bridges.
        case "$vendor:$class" in 0x8086:0x0104*) hint=raid ;; esac
        case "$vendor:$product" in
            0x8086:0x201d|0x8086:0x9a0b|0x8086:0x9a0f|0x8086:0x467f|0x8086:0xa77f|0x8086:0x7d0b|0x8086:0xad0b)
                [ -n "$hint" ] || hint=vmd ;;
        esac
    done
    case "$hint:$count" in
        raid:*) echo "RÅD: Disken er sat til Intel RAID/RST og kan ikke ses af Linux. Skift SATA-tilstand til AHCI i BIOS (F2, Fn+F2 eller Novo-knappen) og start installationen igen." ;;
        vmd:0) echo "RÅD: Intel VMD er slået til. Slå VMD fra i BIOS og start installationen igen." ;;
        *:0) echo "RÅD: Ingen intern disk blev fundet. Tjek i BIOS at disken kan ses, og at SATA-tilstand er AHCI." ;;
        *:[2-9]*|*:1[0-9]*) echo "RÅD: Der er flere mulige interne diske. Tag SD-kort og ekstra diske ud og start igen, eller bed administratoren om en ISO til netop denne computer." ;;
    esac
    echo "Diske:${disks:- ingen}"
    echo "Controllere:${controllers:- ingen}"
    echo "Installationsmedie: ${boot_source:-ukendt}"
}

fail() {
    echo "Elevhub: refusing automatic disk selection: $*" >&2
    inventory >&2
    # The init.d guard displays this error and returns failure directly to
    # partman, which aborts to its main menu before any partitioning starts.
    exit 1
}

[ -d "$sysroot/block" ] || fail "sysfs block inventory is unavailable"
[ -r "$mounts" ] || fail "mount table is unavailable; boot medium cannot be identified"

# Return the parent whole-disk name for a block name (sda1, nvme0n1p1, etc.).
disk_name() {
    n=$1
    case "$n" in
        nvme[0-9]*n[0-9]*p[0-9]*) printf '%s\n' "${n%p[0-9]*}" ;;
        mmcblk[0-9]*p[0-9]*) printf '%s\n' "${n%p[0-9]*}" ;;
        sd[a-z]*[0-9]|vd[a-z]*[0-9]|xvd[a-z]*[0-9]|hd[a-z]*[0-9])
            printf '%s\n' "${n%%[0-9]*}" ;;
        *) printf '%s\n' "$n" ;;
    esac
}

# Resolve the block-device source mounted at /cdrom.  UUID/LABEL sources are
# intentionally rejected because mapping them safely needs udev/blkid.
boot_source=''
while IFS=' ' read -r source mountpoint rest; do
    if [ "$mountpoint" = /cdrom ]; then
        boot_source=$source
        break
    fi
done < "$mounts"
[ -n "$boot_source" ] || fail "installer medium /cdrom is not mounted"
case "$boot_source" in
    /dev/*) boot_name=${boot_source#/dev/} ;;
    *) fail "cannot map /cdrom source $boot_source" ;;
esac
boot_disk=$(disk_name "$boot_name")
case "$boot_disk" in
    loop*|dm-*|md*|sr*|fd*)
        # Optical media is a valid installer source and has no /sys/block
        # whole-disk entry. Other virtual/RAID sources are ambiguous.
        case "$boot_disk" in sr*) ;; *) fail "unresolvable /cdrom source $boot_source" ;; esac ;;
    *) [ -e "$sysroot/block/$boot_disk" ] || fail "boot disk $boot_disk is not present in sysfs" ;;
esac

candidate=''
count=0
for entry in "$sysroot"/block/*; do
    [ -d "$entry" ] || continue
    name=${entry##*/}
    case "$name" in
        # eMMC exposes small special-purpose areas beside the user-data disk.
        # Neither boot partitions nor RPMB are installation targets, even if
        # a kernel reports their removable/ro attributes as writable internal.
        mmcblk[0-9]*boot[0-9]*|mmcblk[0-9]*rpmb*) continue ;;
        sd[a-z]*|nvme[0-9]*n[0-9]*|vd[a-z]*|xvd[a-z]*|hd[a-z]*|mmcblk[0-9]*) ;;
        *) continue ;;
    esac
    [ "$name" = "$boot_disk" ] && continue
    [ -r "$entry/removable" ] && [ "$(cat "$entry/removable")" = 0 ] || continue
    [ -r "$entry/ro" ] && [ "$(cat "$entry/ro")" = 0 ] || continue

    # A USB bridge can report removable=0.  Its resolved sysfs ancestry still
    # contains /usb; exclude it rather than guessing from the device name.
    resolved=$(readlink -f "$entry" 2>/dev/null || true)
    [ -n "$resolved" ] || fail "cannot resolve sysfs path for $name"
    case "$resolved" in
        */usb[0-9]*/*|*/usb[0-9]*) continue ;;
    esac
    count=$((count + 1))
    candidate=$name
done

[ "$count" -eq 1 ] || fail "found $count safe internal disk candidates (boot=$boot_disk)"

selected=/dev/$candidate
if [ "$test_mode" = 1 ]; then
    printf '%s\n' "$selected"
    exit 0
fi
command -v debconf-set >/dev/null 2>&1 || fail "debconf-set is unavailable"
debconf-set partman-auto/disk "$selected"
# Keep GRUB on the same selected whole disk where this template asks for a
# single-disk install.  Failure here must also stop the unattended install.
debconf-set grub-installer/bootdev "$selected"
printf 'Elevhub: selected install disk %s\n' "$selected" >&2

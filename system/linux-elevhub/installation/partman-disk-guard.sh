#!/bin/sh
# Runs directly as partman's FIRST init.d hook, never via preseed_command
# (which swallows command failures). A nonzero init.d status makes partman
# abort with status 10 before running the remaining init/commit scripts.
. /usr/share/debconf/confmodule

log=/var/log/elevhub-disk-selection.log

# Keep labels to one Debconf protocol line and one choice per actual disk.
# A model may contain commas or control characters; neither may create choices.
disk_details() {
    sysfs=/sys/block/${1#/dev/}
    sectors=$(cat "$sysfs/size" 2>/dev/null || echo 0)
    case "$sectors" in ''|*[!0-9]*) sectors=0 ;; esac
    model=$(cat "$sysfs/device/model" "$sysfs/device/name" 2>/dev/null | tr '[:cntrl:],\\' ' ' | tr -s ' ' | cut -c1-48)
    details="$1 — $((sectors / 2097152)) GB — ${model:-ukendt model}"
}

generic_selection() {
    # Listing is read-only. No partman or GRUB target is set before consent.
    candidates=$(sh /cdrom/elevhub/select-install-disk.sh --list-candidates) || return 1
    count=0
    choices='Annuller — vælg ingen disk'
    choices_c=cancel
    selected=''
    for disk in $candidates; do
        count=$((count + 1))
        selected=$disk
        disk_details "$disk"
        choices="$choices, $details"
        choices_c="$choices_c, $disk"
    done
    db_progress STOP >/dev/null 2>&1 || true
    db_x_loadtemplatefile /usr/share/elevhub/disk-selection.templates elevhub || return 1
    if [ "$count" -gt 1 ]; then
        db_subst elevhub/install-disk CHOICES "$choices" || return 1
        db_subst elevhub/install-disk CHOICES_C "$choices_c" || return 1
        db_fset elevhub/install-disk seen false || return 1
        db_set elevhub/install-disk cancel || return 1
        db_input critical elevhub/install-disk || return 1
        if db_go; then
            :
        else
            # cdebconf uses 30 for the user's Back action, not a disk error.
            result=$?
            [ "$result" -ne 30 ] || return 2
            return 1
        fi
        db_get elevhub/install-disk || return 1
        selected=${RET:-cancel}
        [ "$selected" != cancel ] || return 2
        offered=false
        for disk in $candidates; do
            [ "$disk" != "$selected" ] || offered=true
        done
        if [ "$offered" != true ]; then
            echo 'Elevhub: disk choice was not in the offered safe targets' >&2
            return 1
        fi
    fi
    # Validate a fresh inventory, not a label or an index from the old list.
    sh /cdrom/elevhub/select-install-disk.sh --check "$selected" || return 1
    disk_details "$selected"
    db_subst elevhub/disk-wipe-confirm DISK "$details" || return 1
    db_fset elevhub/disk-wipe-confirm seen false || return 1
    db_set elevhub/disk-wipe-confirm false || return 1
    db_input critical elevhub/disk-wipe-confirm || return 1
    if db_go; then
        :
    else
        result=$?
        [ "$result" -ne 30 ] || return 2
        return 1
    fi
    db_get elevhub/disk-wipe-confirm || return 1
    [ "${RET:-false}" = true ] || return 2
    # Revalidate after the dialog too: a disk can disappear or become unsafe
    # while the user is reading it. Only then set BOTH installer targets.
    sh /cdrom/elevhub/select-install-disk.sh --select "$selected" || return 1
}

select_target() {
    if [ -f /cdrom/elevhub/onboarding.py ]; then
        generic_selection
    else
        # Legacy personal/shared media continue to refuse ambiguous disks.
        sh /cdrom/elevhub/select-install-disk.sh
    fi
}

if select_target >"$log" 2>&1; then
    exit 0
else
    result=$?
    # Cancel/No/Back returns to partman without showing a disk failure.
    [ "$result" -ne 2 ] || exit 1
fi

cat "$log" >&2
# Close the "starting partitioner" progress window before asking anything.
db_progress STOP || true
if db_x_loadtemplatefile /usr/share/elevhub/disk-selection.templates elevhub; then
    # cdebconf uses a line protocol: do not send literal embedded newlines.
    # The selector prints its advice and disk/controller inventory; keep all
    # of it, but bound the length so the dialog stays readable.
    details=$(tail -n 12 "$log" | tr '\n' ' ' | cut -c1-1500)
    db_subst elevhub/disk-selection-failed DETAILS "$details" || true
    db_fset elevhub/disk-selection-failed seen false || true
    db_input critical elevhub/disk-selection-failed || true
    db_go || true
fi
# Never allow failure or dismissing the error screen to become success.
exit 1

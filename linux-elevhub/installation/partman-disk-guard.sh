#!/bin/sh
# Runs directly as partman's FIRST init.d hook, never via preseed_command
# (which swallows command failures). A nonzero init.d status makes partman
# abort with status 10 before running the remaining init/commit scripts.
. /usr/share/debconf/confmodule

log=/var/log/elevhub-disk-selection.log
if sh /cdrom/elevhub/select-install-disk.sh >"$log" 2>&1; then
    # Public generic media has no preselected school identity. Require an
    # explicit affirmative Debconf answer before partman can touch the disk.
    # Legacy personal/shared media retain their existing unattended flow.
    if [ -f /cdrom/elevhub/onboarding.py ]; then
        db_get partman-auto/disk || exit 1
        selected=${RET#/dev/}
        sysfs=/sys/block/$selected
        sectors=$(cat "$sysfs/size" 2>/dev/null || echo 0)
        model=$(cat "$sysfs/device/model" "$sysfs/device/name" 2>/dev/null | head -n 1 | tr -s ' ' | cut -c1-48)
        details="Disk: /dev/$selected, størrelse: $((sectors / 2097152)) GB, model: ${model:-ukendt}"
        db_progress STOP >/dev/null 2>&1 || true
        db_x_loadtemplatefile /usr/share/elevhub/disk-selection.templates elevhub || exit 1
        db_subst elevhub/disk-wipe-confirm DISK "$details" || exit 1
        db_fset elevhub/disk-wipe-confirm seen false || exit 1
        db_set elevhub/disk-wipe-confirm false || exit 1
        db_input critical elevhub/disk-wipe-confirm || exit 1
        db_go || exit 1
        db_get elevhub/disk-wipe-confirm || exit 1
        [ "${RET:-false}" = true ] || exit 1
    fi
    exit 0
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

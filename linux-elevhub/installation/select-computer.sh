#!/bin/sh
# Select one machine profile before partman is allowed to inspect or change disks.
# This script is called directly as the first partman init hook.
set -eu
LC_ALL=C
export LC_ALL

if [ "${ELEVHUB_TEST_MODE:-0}" = 1 ]; then
    profiles_root=${ELEVHUB_PROFILES_ROOT:-/cdrom/elevhub/profiles}
    names_file=${ELEVHUB_NAMES_FILE:-$profiles_root/names.txt}
    templates_file=${ELEVHUB_TEMPLATES_FILE:-/usr/share/elevhub/computer-selection.templates}
    state_file=${ELEVHUB_COMPUTER_STATE:-/var/run/elevhub-computer-name}
else
    profiles_root=/cdrom/elevhub/profiles
    names_file=$profiles_root/names.txt
    templates_file=/usr/share/elevhub/computer-selection.templates
    state_file=/var/run/elevhub-computer-name
fi
sentinel='-- vælg computer --'

fail() {
    echo "Elevhub: computer selection failed: $*" >&2
    exit 1
}

if [ "${ELEVHUB_TEST_MODE:-0}" = 1 ]; then
    : "${ELEVHUB_TEST_CONFMODULE:?ELEVHUB_TEST_CONFMODULE is required in test mode}"
    # The fixture supplies the cdebconf functions and records their calls.
    . "$ELEVHUB_TEST_CONFMODULE"
else
    . /usr/share/debconf/confmodule
fi

[ -f "$names_file" ] && [ ! -L "$names_file" ] || fail "profile name list is missing"
[ -f "$templates_file" ] && [ ! -L "$templates_file" ] || fail "selection templates are missing"

tmp_seen=$(mktemp "${TMPDIR:-/tmp}/elevhub-computer-names.XXXXXX")
trap 'rm -f "$tmp_seen"' EXIT HUP INT TERM
choices=$sentinel
count=0
while IFS= read -r name || [ -n "$name" ]; do
    # Names are deliberately ASCII and newline-safe because they become a
    # cdebconf Choices field and a profile directory name.
    case "$name" in
        ''|*[!a-z0-9-]*|[-]*|*[-]|?????????????????????????????????*)
            fail "invalid profile name list entry" ;;
    esac
    grep -Fqx -- "$name" "$tmp_seen" && fail "duplicate profile name"
    printf '%s\n' "$name" >> "$tmp_seen"
    choices="$choices,$name"
    count=$((count + 1))
done < "$names_file"
[ "$count" -gt 0 ] || fail "profile name list is empty"

# Partman may run its init hooks again after returning from a later screen.
# Keep the first confirmed choice for this boot; silently choosing another
# profile on retry would make the hostname and credentials disagree.
if [ -e "$state_file" ]; then
    [ -f "$state_file" ] && [ ! -L "$state_file" ] || fail "selection state is unsafe"
    selected=$(cat "$state_file") || fail "cannot read selection state"
    case "$selected" in
        ''|*[!a-z0-9-]*|[-]*|*[-]|?????????????????????????????????*)
            fail "selection state has an invalid computer name" ;;
    esac
    grep -Fqx -- "$selected" "$tmp_seen" || fail "selection state is not in the profile list"
    profile_dir="$profiles_root/$selected"
    [ -d "$profile_dir" ] && [ ! -L "$profile_dir" ] || fail "selected profile is missing"
    for file in settings.json device.json ca.crt admin.pub; do
        [ -f "$profile_dir/$file" ] && [ ! -L "$profile_dir/$file" ] || fail "selected profile is incomplete"
    done
    db_set netcfg/hostname "$selected" || fail "cannot restore hostname"
    db_set netcfg/get_hostname "$selected" || fail "cannot restore hostname answer"
    exit 0
fi

db_progress STOP >/dev/null 2>&1 || true
db_x_loadtemplatefile "$templates_file" elevhub || fail "cannot load selection dialog"
db_subst elevhub/computer CHOICES "$choices" || fail "cannot set profile choices"
db_fset elevhub/computer seen false || fail "cannot reset profile dialog"
db_set elevhub/computer "$sentinel" || fail "cannot clear profile selection"
db_input critical elevhub/computer || fail "cannot show profile selection"
db_go || fail "profile selection was cancelled"
db_get elevhub/computer || fail "cannot read profile selection"
selected=${RET:-$sentinel}
[ "$selected" != "$sentinel" ] || fail "no computer was selected"
grep -Fqx -- "$selected" "$tmp_seen" || fail "selected computer is not in the profile list"

db_subst elevhub/computer-confirm COMPUTER "$selected" || fail "cannot set confirmation text"
db_fset elevhub/computer-confirm seen false || fail "cannot reset confirmation dialog"
db_set elevhub/computer-confirm false || fail "cannot clear confirmation"
db_input critical elevhub/computer-confirm || fail "cannot show confirmation"
db_go || fail "computer confirmation was cancelled"
db_get elevhub/computer-confirm || fail "cannot read confirmation"
[ "${RET:-false}" = true ] || fail "computer identity was not confirmed"

profile_dir="$profiles_root/$selected"
[ -d "$profile_dir" ] && [ ! -L "$profile_dir" ] || fail "selected profile is missing"
for file in settings.json device.json ca.crt admin.pub; do
    [ -f "$profile_dir/$file" ] && [ ! -L "$profile_dir/$file" ] || fail "selected profile is incomplete"
done

state_dir=${state_file%/*}
[ "$state_dir" != "$state_file" ] || state_dir=.
mkdir -p "$state_dir"
[ ! -L "$state_file" ] || fail "selection state is a symlink"
temporary=$(mktemp "$state_file.XXXXXX")
trap 'rm -f "$tmp_seen" "$temporary"' EXIT HUP INT TERM
umask 077
printf '%s\n' "$selected" > "$temporary"
chmod 600 "$temporary"
mv -f -- "$temporary" "$state_file"

db_set netcfg/hostname "$selected" || fail "cannot set hostname"
db_set netcfg/get_hostname "$selected" || fail "cannot set hostname answer"
exit 0

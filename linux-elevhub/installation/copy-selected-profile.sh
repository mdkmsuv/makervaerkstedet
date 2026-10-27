#!/bin/sh
# Copy only the profile confirmed by select-computer.sh into the target root.
set -eu
LC_ALL=C
export LC_ALL

if [ "${ELEVHUB_TEST_MODE:-0}" = 1 ]; then
    profiles_root=${ELEVHUB_PROFILES_ROOT:-/cdrom/elevhub/profiles}
    names_file=${ELEVHUB_NAMES_FILE:-$profiles_root/names.txt}
    state_file=${ELEVHUB_COMPUTER_STATE:-/var/run/elevhub-computer-name}
    target_root=${ELEVHUB_TARGET_ROOT:-/target}
else
    profiles_root=/cdrom/elevhub/profiles
    names_file=$profiles_root/names.txt
    state_file=/var/run/elevhub-computer-name
    target_root=/target
fi

fail() {
    echo "Elevhub: selected profile copy failed: $*" >&2
    exit 1
}

[ -f "$names_file" ] && [ ! -L "$names_file" ] || fail "profile name list is missing"
[ -f "$state_file" ] && [ ! -L "$state_file" ] || fail "computer selection is missing"
selected=$(cat "$state_file") || fail "cannot read computer selection"
case "$selected" in
    ''|*[!a-z0-9-]*|[-]*|*[-]|?????????????????????????????????*)
        fail "invalid selected computer name" ;;
esac

found=0
while IFS= read -r name || [ -n "$name" ]; do
    case "$name" in
        ''|*[!a-z0-9-]*|[-]*|*[-]|?????????????????????????????????*)
            fail "invalid profile name list entry" ;;
    esac
    if [ "$name" = "$selected" ]; then found=$((found + 1)); fi
done < "$names_file"
[ "$found" -eq 1 ] || fail "selected computer is not uniquely listed"

profile_dir="$profiles_root/$selected"
[ -d "$profile_dir" ] && [ ! -L "$profile_dir" ] || fail "selected profile is missing"
for file in settings.json device.json ca.crt admin.pub; do
    [ -f "$profile_dir/$file" ] && [ ! -L "$profile_dir/$file" ] || fail "selected profile is incomplete"
done

destination="$target_root/etc/elevhub-install"
[ ! -e "$destination" ] || [ -d "$destination" ] && [ ! -L "$destination" ] || fail "target profile directory is unsafe"
umask 077
mkdir -p "$destination"
chmod 700 "$destination"
for file in settings.json device.json ca.crt admin.pub; do
    dest="$destination/$file"
    [ ! -L "$dest" ] || fail "target profile file is a symlink"
    cp "$profile_dir/$file" "$dest"
    chmod 600 "$dest"
done
exit 0

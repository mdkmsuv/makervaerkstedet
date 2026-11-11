#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_package="$script_dir/matchbox-source/matchbox-window-manager_1.2.2+git20200512-2.dsc"
patch_file="$script_dir/matchbox-close-guard.patch"
output_file="$script_dir/bin/matchbox-window-manager"

if [ ! -f "$source_package" ]; then
    echo "missing retained Matchbox source package: $source_package" >&2
    exit 1
fi
if [ ! -f "$patch_file" ]; then
    echo "missing close guard patch: $patch_file" >&2
    exit 1
fi

build_dir=$(mktemp -d "${TMPDIR:-/tmp}/matchbox-close-guard.XXXXXX")
macro_dir=$(mktemp -d "${TMPDIR:-/tmp}/matchbox-macros.XXXXXX")
cleanup() {
    rm -rf "$build_dir" "$macro_dir"
}
trap cleanup EXIT HUP INT TERM

# dpkg-source checks the source archive hashes from the retained .dsc and
# applies Debian's build fixes before our small window-close patch.
dpkg-source -x "$source_package" "$build_dir/source"
(
    cd "$build_dir/source"
    patch -p1 < "$patch_file"

    # Debian's historical configure.ac references this obsolete macro.  The
    # kiosk build does not install GConf schemas, so a no-op conditional is
    # sufficient and keeps the build offline and reproducible.
    cat > "$macro_dir/gconf.m4" <<'EOF'
AC_DEFUN([AM_GCONF_SOURCE_2],[AM_CONDITIONAL([GCONF_SCHEMAS_INSTALL],[false])])
EOF
    autoreconf -fi -I "$macro_dir"

    mkdir build
    cd build
    ../configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var \
        --disable-composite --enable-startup-notification --enable-expat

    # libmb.pc in the Debian source omits the X11 libraries needed by this
    # executable.  Keep the correction local to the generated Makefile.
    sed -i 's/^LIBMB_LIBS = .*/LIBMB_LIBS = -lmb -lX11 -lXext -lXfixes -lXrender/' src/Makefile
    make
)

mkdir -p "$script_dir/bin"
install -m 0755 "$build_dir/source/build/src/matchbox-window-manager" "$output_file"
sha256sum "$output_file"

# Matchbox per-window close guard

This directory contains a small Debian Matchbox Window Manager 1.2.2+
git20200512 patch for the Elevhub kiosk. When both environment variables below
match a normal application window's `WM_CLASS` instance and class, Matchbox
routes the close button and WM close paths (including Matchbox Alt+F4) to the
toolbar's save/discard/continue flow. It sets `_ELEVHUB_FINISH_REQUEST` on the
root window; `browser_close_guard.py` receives it and requests one local
toolbar finish operation. The WM never kills the protected client.
Transient/dialog windows are exempt even if
they have the same `WM_CLASS`, so Chromium file and save dialogs remain
closable.

```text
MB_BLOCK_CLOSE_INSTANCE=usr_local_share_elevhub_start.html
MB_BLOCK_CLOSE_CLASS=Chromium
```

Chromium's own Ctrl+W, Ctrl+Shift+W and Ctrl+Shift+Q shortcuts are handled by
the separate browser guard. This Matchbox patch only owns WM-level close
requests; it does not install global key grabs.

The source is Debian's `1.2.2+git20200512-2` package. The upstream source
archive is retained in `matchbox-source/` and has SHA-256
`a0d869dd182b8e31cd67bd4425db48f66ada4f4163e57b9b796304f5a022abcd`.
Debian's copyright and licensing notice is copied to `debian-copyright`.

## Build

Run `build.sh` in a Debian/Ubuntu environment with the Matchbox build
dependencies installed. It does not fetch anything. It extracts a temporary
copy from the retained source, applies `matchbox-close-guard.patch`, runs the
Autotools build, and writes only the finished executable to `bin/`.

The current finish-request binary, rebuilt using `build.sh`, has SHA-256
`1e12237a37a8e023dca7da641ba68f210c7922a277b4dcaa9b06a4d665802930`.

The focused Xvfb probe is `tests/test_close_guard.py`; it is run by the parent
integration workflow with a private, authenticated display.

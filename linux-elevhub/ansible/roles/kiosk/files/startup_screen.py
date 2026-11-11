#!/usr/bin/env python3
"""The quiet pre-flight screen shown while the kiosk session comes up.

This process deliberately owns no application lifecycle.  It only observes
the X11 windows that the session starts and checks health endpoints explicitly
listed in the local startup configuration.  The screen is an override-redirect
window, so it does not need (and must not take) an input grab.
"""

from __future__ import annotations

import argparse
import dataclasses
import ipaddress
import json
import os
from pathlib import Path
import signal
import threading
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request

from update_status import STATUS_PATH, blocks_power, current_boot_id, message, read_status


DEFAULT_CONFIG = "/etc/elevhub/startup.json"
DEFAULT_MAX_SECONDS = 45.0
MAIN_INSTANCE = "usr_local_share_elevhub_start.html"
MAIN_CLASS = "Chromium"
BAR_INSTANCE = "elevbjaelke"
BAR_CLASS = "Elevbjaelke"
READY_TITLE = "Værkstedet · Makerværkstedet"
BRAND = "#c62b2f"
BRAND_DARK = "#8c1d24"
INK = "#000000"
MUTED = "#000000"
LIGHT = "#000000"
FAILURE_BG = "#f5f5f3"
FONT = "Metrophobic"
FAILURE_TEXT = "Opstarten tager længere tid end forventet. Bed din lærer om hjælp."
THEME_COLORS = {
    "red": ("#c62b2f", "#8c1d24"),
    "yellow": ("#a36a00", "#704600"),
    "green": ("#28734e", "#1a5037"),
}


def apply_theme(path="/etc/elevhub/kiosk.json"):
    """Apply the configured kiosk accent, with a safe red fallback."""
    global BRAND, BRAND_DARK
    theme = "red"
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream).get("theme")
        if value in THEME_COLORS:
            theme = value
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    BRAND, BRAND_DARK = THEME_COLORS[theme]
    return theme


def validate_local_url(url: str) -> bool:
    """Return whether *url* is an HTTP URL addressed to a literal loopback IP.

    Restricting this to literal addresses avoids DNS, proxy and external
    network traffic.  The HTTP client below separately rejects redirects.
    """
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() != "http" or parts.username or parts.password:
            return False
        if parts.fragment or not parts.hostname:
            return False
        address = ipaddress.ip_address(parts.hostname)
        if not address.is_loopback:
            return False
        port = parts.port
        if port is None or not 1 <= port <= 65535:
            return False
        # urlsplit accepts an empty path and unusual control characters; the
        # HTTP client should only receive ordinary, absolute local URLs.
        if any(ord(char) < 0x20 or ord(char) == 0x7f for char in url):
            return False
        return True
    except (ValueError, TypeError):
        return False


def load_readiness_urls(path: str | os.PathLike[str]) -> list[str] | None:
    """Load and validate the local health URL list from a JSON file.

    A malformed or unsafe entry invalidates the complete list.  Silently
    dropping an unsafe URL could otherwise make the startup check appear
    healthy without checking the service the administrator configured.
    """
    try:
        with open(path, encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, ValueError, TypeError):
        return None
    urls = document.get("urls") if isinstance(document, dict) else None
    if not isinstance(urls, list) or not all(isinstance(item, str) for item in urls):
        return None
    if any(not validate_local_url(item) for item in urls):
        return None
    # Preserve order for deterministic diagnostics, while avoiding duplicate
    # requests when a config accidentally repeats an endpoint.
    return list(dict.fromkeys(urls))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def http_ready(urls: list[str], timeout: float = 0.5) -> bool:
    """Return true only when every configured local URL responds with 200.

    ``ProxyHandler({})`` makes the no-proxy rule explicit even on machines
    whose environment contains HTTP(S)_PROXY.  The custom redirect handler
    turns a 3xx response into a failed request instead of following it.
    """
    if urls is None or any(not validate_local_url(url) for url in urls):
        return False
    if not urls:
        return True
    bounded_timeout = max(0.01, min(float(timeout), 0.5))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    )
    for url in urls:
        request = urllib.request.Request(url, method="GET")
        try:
            with opener.open(request, timeout=bounded_timeout) as response:
                if response.status != 200:
                    return False
                # Consume a small bounded amount so the connection can close
                # cleanly without allowing a health endpoint to stream data.
                response.read(4096)
        except urllib.error.HTTPError as error:
            error.close()
            return False
        except (OSError, ValueError, urllib.error.URLError):
            return False
    return True


@dataclasses.dataclass
class TimeoutState:
    """Small, monotonic timeout state that is straightforward to unit test."""

    started_at: float
    max_seconds: float = DEFAULT_MAX_SECONDS

    @classmethod
    def start(cls, now: float | None = None, max_seconds: float = DEFAULT_MAX_SECONDS):
        return cls(time.monotonic() if now is None else float(now), float(max_seconds))

    def expired(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else float(now)
        return current - self.started_at >= self.max_seconds

    def reset(self, now: float | None = None) -> None:
        self.started_at = time.monotonic() if now is None else float(now)


# A descriptive alias is useful to callers that want to name the policy rather
# than its implementation detail.
StartupTimeout = TimeoutState


def _decode_property(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").rstrip("\x00")
    if isinstance(value, (list, tuple)):
        if all(isinstance(item, int) for item in value):
            return bytes(value).decode("utf-8", "replace").rstrip("\x00")
        return "".join(str(item) for item in value).rstrip("\x00")
    return str(value).rstrip("\x00")


class XWindowProbe:
    """Read-only X11 window inspection, imported lazily for headless tests."""

    def __init__(self, display_name: str | None = None):
        from Xlib import X, display, error as xerror

        self.X = X
        self._xerror = xerror
        self._display = display.Display(display_name)
        self._root = self._display.screen().root
        self._wm_class_atom = self._display.intern_atom("WM_CLASS")
        self._wm_state_atom = self._display.intern_atom("WM_STATE")
        self._net_name_atom = self._display.intern_atom("_NET_WM_NAME")
        self._utf8 = self._display.intern_atom("UTF8_STRING")
        self.dead = False

    def close(self) -> None:
        if self._display is not None:
            self._display.close()
            self._display = None

    def _children(self, window):
        try:
            return window.query_tree().children
        except (self._xerror.ConnectionClosedError, self._xerror.DisplayConnectionError, OSError):
            self.dead = True
            return ()
        except Exception:
            # A window can disappear between query_tree and inspection.  That
            # is normal during startup and does not mean the X server is dead.
            return ()

    def _mapped(self, window) -> bool:
        try:
            return (window.get_attributes().map_state == self.X.IsViewable and
                    self._normal_window_state(window))
        except Exception:
            return False

    def _normal_window_state(self, window) -> bool:
        """Treat an absent WM_STATE as normal, but reject iconic windows."""
        try:
            property_value = window.get_full_property(self._wm_state_atom, self.X.AnyPropertyType)
            if property_value is None or not property_value.value:
                return True
            return int(property_value.value[0]) == 1  # NormalState
        except Exception:
            # A window can disappear between the map-state and WM_STATE
            # requests.  It will be observed again on the next poll.
            return False

    def _wm_class(self, window) -> tuple[str, str] | None:
        try:
            value = window.get_wm_class()
        except Exception:
            value = None
        if not value or len(value) != 2:
            return None
        return _decode_property(value[0]), _decode_property(value[1])

    def _window_name(self, window) -> str:
        try:
            property_value = window.get_full_property(self._net_name_atom, self._utf8)
        except Exception:
            property_value = None
        if property_value is None:
            return ""
        return _decode_property(property_value.value)

    def windows(self):
        """Yield mapped top-level and descendant windows currently visible."""
        if self._display is None:
            return
        pending = list(self._children(self._root))
        while pending:
            window = pending.pop()
            pending.extend(self._children(window))
            if self._mapped(window):
                yield window

    def has_ready_windows(self) -> tuple[bool, bool]:
        main = False
        bar = False
        try:
            for window in self.windows():
                classes = self._wm_class(window)
                if classes == (MAIN_INSTANCE, MAIN_CLASS):
                    main = READY_TITLE in self._window_name(window)
                elif classes == (BAR_INSTANCE, BAR_CLASS):
                    bar = True
                if main and bar:
                    return True, True
        except Exception:
            # X server shutdown is treated as not-ready; the Tk process will
            # then close itself on its next failed update rather than loop.
            return False, False
        return main, bar


@dataclasses.dataclass
class _ReadinessJob:
    urls: list[str]
    timeout: float = 0.5
    done: threading.Event = dataclasses.field(default_factory=threading.Event)
    result: bool = False

    def run(self) -> None:
        try:
            self.result = http_ready(self.urls, self.timeout)
        finally:
            self.done.set()


class StartupScreen:
    """Tk view and non-blocking readiness coordinator."""

    def __init__(self, urls: list[str], mapped_file: str | None = None,
                 max_seconds: float = DEFAULT_MAX_SECONDS, display_name: str | None = None):
        import tkinter as tk

        self.tk = tk
        self.urls = urls
        self.mapped_file = Path(mapped_file) if mapped_file else None
        self.timeout = TimeoutState.start(max_seconds=max_seconds)
        self._closed = False
        self._failure_shown = False
        self._mapped_written = False
        self._job: _ReadinessJob | None = None
        self._next_check = 0.0
        self._probe = XWindowProbe(display_name)
        self.root = tk.Tk(className="elevhub-startup-screen")
        self.root.title("Værkstedet starter")
        self.root.configure(bg=LIGHT)
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        try:
            self.root.attributes("-fullscreen", True)
        except tk.TclError:
            pass
        width = self.root.winfo_screenwidth()
        height = self.root.winfo_screenheight()
        self.root.geometry(f"{width}x{height}+0+0")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Map>", self._on_map, add="+")
        self.root.bind("<Destroy>", self._on_destroy, add="+")
        self._build_view()

    def _build_view(self):
        tk = self.tk
        outer = tk.Frame(self.root, bg=LIGHT)
        outer.pack(fill="both", expand=True)
        card = tk.Frame(outer, bg=LIGHT, padx=56, pady=44,
                        highlightthickness=0)
        card.place(relx=0.5, rely=0.5, anchor="center")
        canvas = tk.Canvas(card, width=86, height=86, bg=LIGHT, highlightthickness=0)
        canvas.pack(pady=(0, 22))
        self._outer = outer
        self._card = card
        self._canvas = canvas
        self.heading = tk.Label(card, text="", font=(FONT, 30), bg=LIGHT, fg=INK)
        self.heading.pack()
        self.message = tk.Label(card, text="", font=(FONT, 17), bg=LIGHT, fg=MUTED,
                                wraplength=560, justify="center")
        self.message.pack(pady=(12, 0))
        self.actions = tk.Frame(card, bg=LIGHT)
        self.actions.pack(pady=(26, 0))

    def _show_failure_theme(self):
        for widget in (self.root, self._outer, self._card, self._canvas, self.actions):
            widget.configure(bg=FAILURE_BG)
        self.heading.configure(text="Værkstedet starter", bg=FAILURE_BG, fg=INK)
        self.message.configure(bg=FAILURE_BG, fg=MUTED)

    def _show_wait_theme(self):
        for widget in (self.root, self._outer, self._card, self._canvas, self.actions):
            widget.configure(bg=LIGHT)
        self.heading.configure(text="", bg=LIGHT, fg=INK)
        self.message.configure(text="", bg=LIGHT, fg=MUTED)

    def _on_map(self, event):
        if (event.widget is not self.root or self.mapped_file is None or
                self._mapped_written):
            return
        try:
            self.mapped_file.parent.mkdir(parents=True, exist_ok=True)
            self.mapped_file.write_text(f"{os.getpid()}\n", encoding="ascii")
            self._mapped_written = True
        except OSError:
            pass

    def _on_destroy(self, _event):
        # Tk may already have torn down the Tcl interpreter while delivering
        # Destroy.  Do not call back into it here.
        if _event.widget is self.root:
            self._closed = True

    def _show_failure(self):
        if self._failure_shown:
            return
        self._failure_shown = True
        self._show_failure_theme()
        self.message.configure(text=FAILURE_TEXT)
        self.retry_button = self.tk.Button(self.actions, text="Prøv igen", command=self.retry,
                                           font=(FONT, 14), bg=BRAND, fg="white",
                                           activebackground=BRAND_DARK, activeforeground="white",
                                           relief="flat", bd=0, padx=22, pady=9)
        self.retry_button.pack(side="left", padx=6)
        self.open_workshop_button = self.tk.Button(self.actions, text="Åbn værkstedet", command=self.close,
                                                   font=(FONT, 14), bg="white", fg=INK,
                                                   activebackground="#eeeeec", relief="solid", bd=1,
                                                   padx=22, pady=8)
        self.open_workshop_button.pack(side="left", padx=6)

    def retry(self):
        self.timeout.reset()
        self._failure_shown = False
        for child in self.actions.winfo_children():
            child.destroy()
        self._show_wait_theme()
        self._next_check = 0.0

    def _start_job(self):
        if self._job is not None and not self._job.done.is_set():
            return
        if time.monotonic() < self._next_check:
            return
        self._job = _ReadinessJob(self.urls)
        threading.Thread(target=self._job.run, name="startup-readiness", daemon=True).start()
        self._next_check = time.monotonic() + 0.8

    def _tick(self):
        if self._closed:
            return
        try:
            # Override-redirect windows are outside the WM's normal stacking
            # policy.  Keep this view above newly mapped application windows
            # without taking an input grab.
            self.root.lift()
            self.root.wm_attributes("-topmost", True)
            update_value = read_status(STATUS_PATH, boot_id=current_boot_id())
            update_message = message(update_value)
            update_active = bool(update_value and
                                 (blocks_power(update_value) or
                                  update_value.get('status') == 'recovery_required'))
            if update_message and not self._failure_shown:
                self.heading.configure(text="Makerværkstedet opdateres", bg=LIGHT, fg=INK)
                self.message.configure(text=update_message, bg=LIGHT, fg=INK)
            main, bar = self._probe.has_ready_windows()
            if self._probe.dead:
                self.close()
                return
            if self._job is not None and self._job.done.is_set():
                if main and bar and self._job.result and not update_active:
                    self.close()
                    return
                self._job = None
            self._start_job()
            if self.timeout.expired() and not update_active:
                self._show_failure()
            self.root.after(100, self._tick)
        except Exception:
            self.close()

    def run(self):
        self.root.after(100, self._tick)
        self.root.mainloop()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._probe.close()
        except (OSError, self._probe._xerror.ConnectionClosedError,
                self._probe._xerror.DisplayConnectionError):
            pass
        try:
            self.root.destroy()
        except self.tk.TclError:
            pass


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--mapped-file")
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS,
                        help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    urls = load_readiness_urls(args.config)
    apply_theme()
    # An unavailable or unsafe config must never turn into a false ready state.
    screen = None
    try:
        screen = StartupScreen(urls, args.mapped_file, args.max_seconds)
        signal.signal(signal.SIGTERM, lambda _signum, _frame: screen.root.after_idle(screen.close))
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, lambda _signum, _frame: screen.root.after_idle(screen.close))
        screen.run()
    except (ImportError, OSError, RuntimeError) as exc:
        print(f"startup screen unavailable: {exc}", flush=True)
        return 1
    finally:
        if screen is not None:
            screen.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

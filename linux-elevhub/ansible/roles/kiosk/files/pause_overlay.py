#!/usr/bin/env python3
"""Black pause screen with an X11-wide keyboard/pointer grab.

Open applications stay alive. A teacher's release or the server's bounded
pause deadline releases input; a short network outage must not unlock it.
This controls the kiosk session, not physical power or an administrator's SSH.
"""
from __future__ import annotations

import math
import time

from pause_safety import PauseSafety, safety_clock


class PauseOverlay:
    def __init__(self, host, *, clock=safety_clock, wall_clock=time.time, safety=None):
        self.host, self.clock, self.wall_clock = host, clock, wall_clock
        self.window = None
        self._version = None
        self._shown = False
        self._pause = False
        self._closure = False
        self._workshop = 'værkstedet'
        self._message = ''
        self._deadline = None
        self._safety = safety if safety is not None else PauseSafety()
        self._pause_key = self._armed_key = self._failed_key = None
        self._title = self._body = self._timer = None

    @staticmethod
    def _number(value):
        try:
            return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
        except OverflowError:
            return False

    @classmethod
    def _valid_state(cls, state):
        if not isinstance(state, dict) or type(state.get('paused')) is not bool:
            return None
        if type(state.get('version')) is not int or state['version'] < 0:
            return None
        if state['paused'] and not cls._number(state.get('until')):
            return None
        if state['paused'] and 'remaining' in state and (
                not cls._number(state['remaining']) or state['remaining'] < 0):
            return None
        return state

    def _build(self, title, message):
        import tkinter as tk

        win = self.window = tk.Toplevel(self.host)
        win.withdraw()
        win.title('Skærmpause · Elevhub')
        win.overrideredirect(True)
        win.attributes('-topmost', True)
        # Matchbox silently ignores fullscreen on override-redirect windows.
        # Explicit geometry prevents the old 200x200 window in the corner.
        width, height = self.host.winfo_screenwidth(), self.host.winfo_screenheight()
        win.geometry(f'{width}x{height}+0+0')
        win.configure(background='#080808', cursor='none')
        win.protocol('WM_DELETE_WINDOW', lambda: None)
        for event in ('<KeyPress>', '<KeyRelease>', '<ButtonPress>', '<ButtonRelease>', '<Motion>'):
            win.bind(event, lambda _event: 'break')

        frame = tk.Frame(win, bg='#080808')
        frame.place(relx=.5, rely=.5, anchor='center', relwidth=.86)
        wrap = max(160, int(width * .8))
        self._title = tk.Label(frame, text=title, font=('Metrophobic', 32),
                               bg='#080808', fg='white', wraplength=wrap)
        self._title.pack()
        self._body = tk.Label(frame, text=message, font=('Metrophobic', 20),
                              bg='#080808', fg='#e1e1e1', justify='center', wraplength=wrap)
        if not self._pause:
            self._body.pack(pady=10)
        win.deiconify()
        win.update_idletasks()

    def _secure(self):
        """Acknowledge only a mapped full-screen window with the required grab.

        Menus in another app can temporarily hold a grab. Retry on the Tk loop,
        reporting shown=False until the screen and input lock both succeed.
        """
        import tkinter as tk

        self._shown = False
        if self.window is None:
            return
        if self._pause and not self._safety.heartbeat():
            self._safety.block()
            self._failed_key = self._pause_key
            self._pause = False
            self._destroy()
            return
        try:
            win = self.window
            width, height = self.host.winfo_screenwidth(), self.host.winfo_screenheight()
            if (win.winfo_width(), win.winfo_height(), win.winfo_x(), win.winfo_y()) != (width, height, 0, 0):
                win.geometry(f'{width}x{height}+0+0')
                win.update_idletasks()
            win.lift()
            if not win.winfo_viewable():
                return
            win.focus_force()
            required = 'global' if self._pause else 'local'
            if win.grab_status() != required:
                if self._pause:
                    win.grab_set_global()
                else:
                    win.grab_set()
            self._shown = (win.grab_status() == required and
                           (win.winfo_width(), win.winfo_height()) == (width, height))
        except tk.TclError:
            self._shown = False

    def _destroy(self):
        if self._timer is not None:
            try:
                self.host.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        if self.window is not None:
            import tkinter as tk
            try:
                self.window.grab_release()
            except tk.TclError:
                pass  # Destroying the window also releases its grab.
            try:
                self.window.destroy()
            except tk.TclError:
                # A destroyed host/window has already released its X resources.
                # If it still exists, leave the independent watchdog armed.
                try:
                    if self.window.winfo_exists():
                        raise
                except tk.TclError as exc:
                    if 'application has been destroyed' not in str(exc):
                        raise
        self.window = self._title = self._body = None
        self._shown = False
        self._safety.stop()
        self._armed_key = None

    def _refresh_text(self):
        self._timer = None
        self.check_stale()
        if self.window is None:
            return
        self._secure()
        if self.window is not None and self._timer is None:
            self._timer = self.host.after(250, self._refresh_text)

    def _show(self, title, message):
        if not self._pause and self._armed_key is not None:
            self._destroy()
        if self._pause and self._armed_key != self._pause_key:
            # Release the previous grab before changing its watchdog lease.
            self._destroy()
            deadline = self._safety.arm(self._pause_key, self._deadline)
            if deadline is None:
                self._failed_key = self._pause_key
                self._pause = False
                return
            self._deadline = deadline
            self._armed_key = self._pause_key
        if self.window is None:
            self._build(title, message)
        else:
            self._title.configure(text=title)
            self._body.configure(text=message)
        if self._pause:
            self._body.pack_forget()
        else:
            self._body.pack(pady=10)
        self._secure()
        if self.window is not None and self._timer is None:
            self._timer = self.host.after(250, self._refresh_text)

    def _render(self):
        if self._pause:
            self._show('Skærmpause', '')
        elif self._closure:
            self._show('Værkstedet er midlertidigt lukket',
                       f'{self._workshop.capitalize()} åbner igen, når læreren giver besked.')
        else:
            self._destroy()

    def update_pause(self, state):
        state = self._valid_state(state)
        if state is None or (self._version is not None and state['version'] < self._version):
            return
        if state['paused']:
            self._pause_key = [state['version'], state['until']]
            # Server-relative time avoids clock skew. A repeated snapshot can
            # shorten a deadline but cannot restart or extend the same pause.
            remaining = state.get('remaining', max(0, state['until'] - self.wall_clock()))
            deadline = self.clock() + min(7200, remaining)
            if state['version'] == self._version and self._deadline is not None:
                deadline = min(deadline, self._deadline)
            self._deadline = deadline
            self._pause = deadline > self.clock() and self._pause_key != self._failed_key
        else:
            self._pause = False
            self._deadline = None
        self._version = state['version']
        self._message = state.get('message', '') if isinstance(state.get('message', ''), str) else ''
        self._render()

    def update_workshop_closed(self, closed, workshop='værkstedet'):
        self._closure = bool(closed)
        self._workshop = workshop
        self._render()

    def offline(self):
        """Release server-controlled pause/closure while the hub is unreachable."""
        self._pause = False
        self._deadline = None
        self._closure = False
        self._render()

    def check_stale(self):
        """Release a pause when its bounded server-issued deadline expires."""
        if self._pause and self._deadline is not None and self.clock() >= self._deadline:
            self._pause = False
            self._render()

    def acknowledgement(self):
        if self._version is None:
            return None
        return {'version': self._version, 'shown': bool(self._shown and self._pause)}

    @property
    def paused(self):
        return self._pause


__all__ = ['PauseOverlay']

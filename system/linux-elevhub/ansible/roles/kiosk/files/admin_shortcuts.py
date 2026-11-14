"""Small X11 global shortcut, dispatched on Tk's own event loop.

Passive grabs work while another application has focus. No input injection,
console switching or privilege escalation belongs in this module.
"""
class GlobalShortcut:
    def __init__(self, root, key, callback):
        from Xlib import X, XK, display
        self.root, self.callback = root, callback
        self.connection = display.Display()
        self.release_timer = None
        self.held = False
        self.closed = False
        self.keycode = self.connection.keysym_to_keycode(XK.string_to_keysym(key))
        window = self.connection.screen().root
        numlock = self.connection.keysym_to_keycode(XK.string_to_keysym('Num_Lock'))
        num_mask = 0
        for index, codes in enumerate(self.connection.get_modifier_mapping()):
            if numlock in codes:
                num_mask |= 1 << index
        errors = []
        for locks in {0, X.LockMask, num_mask, X.LockMask | num_mask}:
            window.grab_key(self.keycode, X.ControlMask | X.Mod1Mask | locks,
                            False, X.GrabModeAsync, X.GrabModeAsync,
                            onerror=lambda error, _request: errors.append(error))
        self.connection.sync()
        if errors:
            self.connection.close()
            raise RuntimeError('Admin-genvejen kunne ikke reserveres.')
        # python-xlib may buffer events while reading protocol replies, so fd
        # readiness alone is insufficient. Drain its queue on Tk's event loop.
        self.poll_timer = root.after(25, self._events)
        root.bind('<Destroy>', self._destroyed, add='+')

    def _events(self, *_):
        from Xlib import X
        while not self.closed and self.connection.pending_events():
            event = self.connection.next_event()
            if event.type not in (X.KeyRelease, X.KeyPress) or event.detail != self.keycode:
                continue
            if event.type == X.KeyRelease:
                if self.release_timer is not None:
                    self.root.after_cancel(self.release_timer)
                # X11 autorepeat may emit release/press pairs. Reset only after
                # a genuine release; the next repeat press cancels this timer.
                self.release_timer = self.root.after(50, self._released)
            elif event.type == X.KeyPress:
                if self.release_timer is not None:
                    self.root.after_cancel(self.release_timer)
                    self.release_timer = None
                if not self.held:
                    self.held = True
                    self.callback()
        if not self.closed:
            self.poll_timer = self.root.after(25, self._events)

    def _released(self):
        self.release_timer = None
        self.held = False

    def _destroyed(self, event):
        if event.widget is self.root:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.release_timer is not None:
            self.root.after_cancel(self.release_timer)
        self.root.after_cancel(self.poll_timer)
        self.connection.close()

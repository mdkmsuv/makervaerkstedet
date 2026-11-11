#!/usr/bin/python3
"""Route Chromium's close shortcuts through finish; replay other input.

The window manager forwards X and Alt+F4 through a root property. Browser-
internal shortcuts do not reach it, so these few keys use synchronous passive
X11 grabs. Every protected close requests the toolbar's common finish flow.
Replayed events retain native delivery; no synthetic events are injected.
"""
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workshop_launcher import request_navigation

INSTANCE = 'usr_local_share_elevhub_start.html'
CLASS = 'Chromium'
RESET_ATOM = '_ELEVHUB_ALLOW_CLOSE_ONCE'
FINISH_ATOM = '_ELEVHUB_FINISH_REQUEST'


def blocked(pair, transient, key, shift):
    if transient:
        return False
    if key == 'q' and shift:
        # Chromium's quit command affects every window in the same process.
        return bool(pair and pair[1] == CLASS)
    return key in ('w', 'F4') and pair == (INSTANCE, CLASS)


def main():
    from Xlib import X, XK, display
    connection = display.Display()
    root = connection.screen().root
    reset_atom = connection.intern_atom(RESET_ATOM)
    finish_atom = connection.intern_atom(FINISH_ATOM)
    root.change_attributes(event_mask=X.PropertyChangeMask)
    active_atom = connection.intern_atom('_NET_ACTIVE_WINDOW')
    dialog_atom = connection.intern_atom('_NET_WM_WINDOW_TYPE_DIALOG')
    type_atom = connection.intern_atom('_NET_WM_WINDOW_TYPE')
    numlock = connection.keysym_to_keycode(XK.string_to_keysym('Num_Lock'))
    num_mask = 0
    for index, codes in enumerate(connection.get_modifier_mapping()):
        if numlock in codes:
            num_mask |= 1 << index
    keycodes = {key: connection.keysym_to_keycode(XK.string_to_keysym(key)) for key in ('w', 'q', 'F4')}
    failures = []
    for key, shift in (('w', False), ('w', True), ('q', True), ('F4', False)):
        base = X.ControlMask | (X.ShiftMask if shift else 0)
        for locks in {0, X.LockMask, num_mask, X.LockMask | num_mask}:
            root.grab_key(keycodes[key], base | locks, False, X.GrabModeAsync, X.GrabModeSync,
                          onerror=lambda error, _request: failures.append(error))
    connection.sync()
    if failures:
        raise RuntimeError('Could not reserve the workshop close shortcuts.')
    print('READY: workshop close shortcuts protected', flush=True)

    def focused_window():
        window = connection.get_input_focus().focus
        if not hasattr(window, 'get_wm_class'):
            active = root.get_full_property(active_atom, X.AnyPropertyType)
            if not active or not len(active.value) or not active.value[0]:
                return None, None, False
            window = connection.create_resource_object('window', int(active.value[0]))
        for _ in range(16):
            if window.id == root.id:
                return None, None, False
            pair = window.get_wm_class()
            if pair:
                kind = window.get_full_property(type_atom, X.AnyPropertyType)
                transient = window.get_wm_transient_for() is not None or bool(kind and dialog_atom in kind.value)
                return window, pair, transient
            window = window.query_tree().parent
        return None, None, False

    while True:
        event = connection.next_event()
        if event.type == X.PropertyNotify and event.atom == finish_atom and event.state == X.PropertyNewValue:
            root.delete_property(finish_atom)
            request_navigation()
            connection.flush()
            continue
        if event.type != X.KeyPress:
            continue
        suppress = False
        try:
            window, pair, transient = focused_window()
            key = next((name for name, code in keycodes.items() if code == event.detail), '')
            suppress = blocked(pair, transient, key, bool(event.state & X.ShiftMask))
            if suppress and key == 'w' and window is not None:
                permission = window.get_full_property(reset_atom, X.AnyPropertyType)
                if permission and len(permission.value) == 1:
                    expiry = int(permission.value[0])
                    now = time.time()
                    window.delete_property(reset_atom)
                    if now <= expiry <= now + 6:
                        # The toolbar's existing recovery path explicitly requests one reset.
                        suppress = False
        except Exception:
            # A disappearing/focusing client must never leave the keyboard frozen.
            suppress = False
        connection.allow_events(X.AsyncKeyboard if suppress else X.ReplayKeyboard, event.time)
        connection.flush()
        # Release the keyboard before disk I/O or starting the finish flow.
        if suppress:
            request_navigation()


if __name__ == '__main__':
    main()

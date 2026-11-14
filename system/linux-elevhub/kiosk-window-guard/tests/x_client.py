#!/usr/bin/env python3
import argparse
import sys
import time
from Xlib import X, Xatom, display, protocol

parser = argparse.ArgumentParser()
parser.add_argument('--instance', required=True)
parser.add_argument('--class-name', required=True)
parser.add_argument('--title', default='probe')
parser.add_argument('--transient-for', type=lambda value: int(value, 0))
args = parser.parse_args()

dpy = display.Display()
root = dpy.screen().root
win = root.create_window(80, 100, 700, 400, 1,
                        dpy.screen().root_depth, X.InputOutput,
                        X.CopyFromParent, background_pixel=0xeeeeee)
win.set_wm_name(args.title)
win.set_wm_class(args.instance, args.class_name)
wm_delete = dpy.intern_atom('WM_DELETE_WINDOW')
wm_protocols = dpy.intern_atom('WM_PROTOCOLS')
win.set_wm_protocols([wm_delete])
if args.transient_for is not None:
    win.set_wm_transient_for(dpy.create_resource_object('window', args.transient_for))
win.map()
dpy.sync()
print(win.id, flush=True)
while True:
    event = dpy.next_event()
    if event.type == X.ClientMessage and event.client_type == wm_protocols:
        # Python-Xlib represents ClientMessage data as (format, array), not
        # the old Xlib ``data.l`` attribute.
        if event.data[1][0] == wm_delete:
            print('WM_DELETE_WINDOW', flush=True)
            sys.exit(0)

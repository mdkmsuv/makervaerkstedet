/* 
 *  Matchbox Window Manager - A lightweight window manager not for the
 *                            desktop.
 *
 *  Authored By Matthew Allum <mallum@o-hand.com>
 *
 *  Copyright (c) 2002, 2004 OpenedHand Ltd - http://o-hand.com
 *
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *
 */

#ifndef _MB_KEYS_H_
#define _MB_KEYS_H_

#ifndef NO_KBD

#include "structs.h"
#include "misc.h"

Bool keys_add_entry(Wm *w, char *keysstr, int action, int idata, char *sdata) ;

Bool keys_load_config(Wm *w);

void keys_get_modifiers(Wm *w);

void keys_reinit(Wm *w);

void keys_init(Wm *w);

void keys_grab(Wm *w, Bool want_ungrab);

#endif

#endif

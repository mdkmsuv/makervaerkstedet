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

#ifndef _DESKTOP_CLIENT_H_
#define _DESKTOP_CLIENT_H_

#include "structs.h"
#include "base_client.h"
#include "wm.h"
#include "main_client.h"
#include "misc.h"
#include "select_client.h"

Client* desktop_client_new(Wm *w, Window win);

void 
desktop_client_reparent(Client *c);

void 
desktop_client_move_resize(Client *c);

void 
desktop_client_get_coverage(Client *c, int *x, int *y, int *w, int *h);

void 
desktop_client_configure(Client *c);

void 
desktop_client_show(Client *c);

void
desktop_client_destroy(Client *c);

#endif

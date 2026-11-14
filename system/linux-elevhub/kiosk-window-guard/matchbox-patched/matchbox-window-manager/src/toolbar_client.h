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

#ifndef _TOOLBAR_CLIENT_H_
#define _TOOLBAR_CLIENT_H_

#include "structs.h"
#include "client_common.h"
#include "base_client.h"
#include "main_client.h"

Client* 
toolbar_client_new(Wm *w, Window win);

void 
toolbar_client_configure(Client *c);

void 
toolbar_client_button_press(Client *c, XButtonEvent *e);

void 
toolbar_client_show(Client *c);

void 
toolbar_client_hide(Client *c);

void 
toolbar_client_move_resize(Client *c);

void 
toolbar_client_get_coverage(Client *c, int *x, int *y, int *w, int *h);

void 
toolbar_client_destroy(Client *c);

void 
toolbar_client_reparent(Client *c);

void 
toolbar_client_redraw(Client *c, Bool use_cache);

int 
toolbar_win_offset(Client *c);

#endif

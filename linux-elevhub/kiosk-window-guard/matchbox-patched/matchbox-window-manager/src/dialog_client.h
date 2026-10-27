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

#ifndef _DIALOG_CLIENT_H_
#define _DIALOG_CLIENT_H_

#include "structs.h"
#include "wm.h"
#include "client_common.h"
#include "base_client.h"
#include "main_client.h"
#include "dialog_client.h"
#include "misc.h"

Client* 
dialog_client_new (Wm *w, Window win, Client *trans);

void 
dialog_client_get_coverage (Client *c, int *x, int *y, int *w, int *h);

void 
dialog_client_move_resize (Client *c);

void 
dialog_client_reparent (Client *c);

void 
dialog_client_hide (Client *c);

void 
dialog_client_show (Client *c);

void 
dialog_client_configure (Client *c);

void 
dialog_client_redraw (Client *c, Bool use_cache);

void 
dialog_client_button_press (Client *c, XButtonEvent *e);

void
dialog_client_iconize(Client *c);

void 
dialog_client_destroy (Client *c);

/* dialog only methods */

int  
dialog_client_title_height (Client *c);

Bool
dialog_constrain_geometry(Client *c,
			  int    *req_x,
			  int    *req_y,
			  int    *req_width,
			  int    *req_height);

#endif

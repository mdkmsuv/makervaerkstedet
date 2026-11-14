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

#ifndef _SELECT_CLIENT_H_
#define _SELECT_CLIENT_H_

#include "structs.h"
#include "base_client.h"
#include "client_common.h"
#include "main_client.h"
#include "select_client.h"
#include "dockbar_client.h"
#include "wm.h"
#include "misc.h"



Client* 
select_client_new (Wm *w);

void 
select_client_destroy (Client *c);

void 
select_client_calc_dim (Wm *w, int *width, int *height);

void 
select_client_button_press (Client *c, XButtonEvent *e);

void 
select_client_init (Client *c);

void 
select_client_reparent (Client *c);

void 
select_client_redraw (Client *c, Bool use_cache);

void 
select_client_event_loop (Client *c, struct list_item *button_item_cur );


#endif




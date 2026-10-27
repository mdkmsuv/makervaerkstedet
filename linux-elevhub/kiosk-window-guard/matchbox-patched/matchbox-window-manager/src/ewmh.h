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

#ifndef _EWMH_H_
#define _EWMH_H_

#include "structs.h" 
#include "wm.h"

 /* Number of failed pending pings to kill a ping supporting app on */
#define PING_PENDING_MAX 2 

 /* Time in seconds between each hung app check */
#define PING_CHECK_FREQ  2 

/* Max num of pings to send to an app - used only when in aggresive mode */
#define PING_CHECK_DURATION 5

/* Non aton defines */
#define _NET_WM_STATE_REMOVE        0    /* remove/unset property */
#define _NET_WM_STATE_ADD           1    /* add/set property */
#define _NET_WM_STATE_TOGGLE        2    /* toggle property  */

void 
ewmh_init (Wm *w);

void 
ewmh_init_props (Wm *w);

void
ewmh_update_lists(Wm *w);

void
ewmh_update_desktop_hint(Wm *w);

void
ewmh_update_rects(Wm *w);

void 
ewmh_set_active (Wm *w);

void 
ewmh_set_current_app_window(Wm *w);

int  
ewmh_handle_root_message (Wm *w, XClientMessageEvent *e);

unsigned char *
ewmh_get_utf8_prop (Wm *w, Window win, Atom req_atom);

void 
ewmh_state_set(Client *c);

Bool 
ewmh_state_check (Client *c, Atom atom_state_wanted);

int
ewmh_get_user_time (Client *c);

void 
ewmh_set_allowed_actions (Wm *w, Client *c);

void
ewmh_ping_client_start (Client *c);

void
ewmh_ping_client_stop (Client *c);

void 
ewmh_hung_app_check (Wm *w);

#ifndef REDUCE_BLOAT
unsigned long *
ewmh_get_icon_prop_data (Wm *w, Window win);
#endif

int 
ewmh_utf8_len(unsigned char *str);

int 
ewmh_utf8_get_byte_cnt(unsigned char *str, int num_chars);

Bool 
ewmh_utf8_validate(unsigned char *str, int max_len);

#ifdef USE_XSYNC

void
ewmh_sync_init(Wm *w);

void
ewmh_sync_handle_event(Wm *w, XSyncAlarmNotifyEvent *ev);

Bool
ewmh_sync_client_move_resize(Client *client);

Bool
ewmh_sync_client_init_counter(Client *client);

#endif /* USE_XSYNC */

#endif

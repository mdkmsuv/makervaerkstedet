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

#ifndef _MB_SESSION_H_
#define _MB_SESSION_H_

#include "structs.h"
#include "wm.h"
#include "config.h"

#if USE_SM

#include <X11/SM/SMlib.h>

void
sm_process_event(Wm *w);

Bool
sm_connect(Wm *w);

#endif /* USE_SM */

#endif

/* SPDX-License-Identifier: MIT */
#ifndef A22X_CHARGER_UI_H
#define A22X_CHARGER_UI_H

/* Single-owner interface. No charger/PMIC settings are changed.
 * open: 0 on success, -1 with errno on a retryable device/setup failure.
 * render: capacity outside 0..100 is unknown; connected is -1, 0 or 1.
 * render: 0 on success, -1 with errno on a backlight/device error.
 * close: best-effort restore of the original VT mode and brightness.
 * No function writes diagnostics to stdout or stderr.
 */
int charger_ui_open(void);
int charger_ui_render(int capacity, int connected, int screen_on);
void charger_ui_close(void);

#endif

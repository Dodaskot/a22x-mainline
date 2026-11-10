// SPDX-License-Identifier: MIT
package org.a22x.installer;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

/** Keeps the recovery's language, time zone and clock in step with the phone: after a change of
 *  language, zone or time, and after a start, the hand-over file is written again. Only after the
 *  user has granted root to this app once, and only while "Keep in sync" is on. */
public class SyncReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        SharedPreferences prefs = context.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE);
        if (!prefs.getBoolean(MainActivity.KEY_SYNC, true) || !prefs.getBoolean(MainActivity.KEY_ROOT_GRANTED, false)) return;
        final PendingResult pending = goAsync();
        final Context app = context.getApplicationContext();
        new Thread(() -> {
            try {
                RecoveryStore.sendHandoff(app, null, null);
            } catch (Exception ignored) {
                // The next change or start tries again.
            } finally {
                pending.finish();
            }
        }).start();
    }
}

// SPDX-License-Identifier: MIT
package org.a22x.installer;

/** Prints the app's root scripts for a test on a device with loop devices (tools/test-scripts-on-phone.sh):
 *    flash LABEL IMAGE SIZE SHA256 BACKUP_DIR PARTITION_SIZE   the flash script for a test partition LABEL
 *    handoff FILE                                  the script that copies FILE as the hand-over */
public final class ScriptsMain {
    public static void main(String[] args) {
        if (args[0].equals("flash")) {
            System.out.print(Scripts.flash(args[1], args[2], Long.parseLong(args[3]), args[4], args[5], Long.parseLong(args[6]), "", args[1]));
        } else if (args[0].equals("handoff")) {
            System.out.print(Scripts.CACHE_OPEN + "cat " + Root.quote(args[1]) + " > \"$D/handoff.json.part\" && mv \"$D/handoff.json.part\" \"$D/handoff.json\""
                    + " && echo A22X:OK" + Scripts.CACHE_CLOSE);
        } else {
            throw new IllegalArgumentException(args[0]);
        }
    }
}

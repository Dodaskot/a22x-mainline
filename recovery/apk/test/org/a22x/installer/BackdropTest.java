// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import javax.imageio.ImageIO;

/** The app's backdrop against the recovery's (2026-11-10): BackdropMath on the exported PNG must
 *  give every pixel that the recovery's canvas_backdrop() gives at 1080 x 2400 (backdrop_ref.c).
 *
 *    java BackdropTest RES_DIR DAYLIGHT_REF AURORA_REF WIDTH HEIGHT */
public final class BackdropTest {
    public static void main(String[] args) throws IOException {
        int width = Integer.parseInt(args[3]), height = Integer.parseInt(args[4]);
        String[] names = {"daylight", "aurora"};
        for (int n = 0; n < 2; n++) {
            BufferedImage png = ImageIO.read(new File(args[0], "drawable-nodpi/backdrop_" + names[n] + ".png"));
            int sw = png.getWidth(), sh = png.getHeight();
            int[] src = png.getRGB(0, 0, sw, sh, null, 0, sw);
            final ByteBuffer ref = ByteBuffer.wrap(Files.readAllBytes(new File(args[1 + n]).toPath())).order(ByteOrder.LITTLE_ENDIAN);
            if (ref.capacity() != width * height * 4) throw new AssertionError(names[n] + ": reference has " + ref.capacity() + " bytes");
            final int[] differ = {0};
            final String[] first = {null};
            BackdropMath.stretch(src, sw, sh, width, height, (y, pixels) -> {
                for (int x = 0; x < width; x++) {
                    int want = ref.getInt((y * width + x) * 4) & 0xffffff, got = pixels[x] & 0xffffff;
                    if (want != got) {
                        if (first[0] == null) first[0] = String.format("(%d,%d) %06x instead of %06x", x, y, got, want);
                        differ[0]++;
                    }
                }
            });
            if (differ[0] != 0) throw new AssertionError(names[n] + ": " + differ[0] + " pixels differ, first " + first[0]);
            System.out.println("{\"backdrop\":\"" + names[n] + "\",\"pixels\":" + width * height + ",\"identical\":true}");
        }
    }
}

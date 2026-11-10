// SPDX-License-Identifier: MIT
package org.a22x.installer;

/** canvas_backdrop() of the recovery's native_canvas.h in plain Java (2026-11-10): a small soft
 *  image stretched bilinearly, with the same 8x8 ordered dither added before rounding so the
 *  gradient does not band. Plain Java so that BackdropTest compares it, pixel for pixel, with
 *  the recovery's own C function on a computer. */
final class BackdropMath {
    interface Rows {
        void row(int y, int[] pixels);
    }

    private static final int[][] BAYER = {{0, 32, 8, 40, 2, 34, 10, 42}, {48, 16, 56, 24, 50, 18, 58, 26}, {12, 44, 4, 36, 14, 46, 6, 38},
            {60, 28, 52, 20, 62, 30, 54, 22}, {3, 35, 11, 43, 1, 33, 9, 41}, {51, 19, 59, 27, 49, 17, 57, 25}, {15, 47, 7, 39, 13, 45, 5, 37},
            {63, 31, 55, 23, 61, 29, 53, 21}};

    private BackdropMath() {
    }

    /** SRC (SW x SH pixels, 0xRRGGBB) to WIDTH x HEIGHT, handed to ROWS one line at a time as
     *  opaque 0xAARRGGBB. The arithmetic is the C function's, in the same float order. */
    static void stretch(int[] src, int sw, int sh, int width, int height, Rows rows) {
        int[] x0 = new int[width], x1 = new int[width];
        float[] tx = new float[width];
        for (int x = 0; x < width; x++) {
            float fx = ((float) x + .5f) * (float) sw / (float) width - .5f;
            if (fx < 0) fx = 0;
            x0[x] = (int) fx;
            x1[x] = x0[x] + 1 < sw ? x0[x] + 1 : x0[x];
            tx[x] = fx - (float) x0[x];
        }
        float[][] top = new float[3][width], bottom = new float[3][width];
        int[] row = new int[width];
        int mixed = -1;
        for (int y = 0; y < height; y++) {
            float fy = ((float) y + .5f) * (float) sh / (float) height - .5f;
            if (fy < 0) fy = 0;
            int y0 = (int) fy, y1 = y0 + 1 < sh ? y0 + 1 : y0;
            float ty = fy - (float) y0, keep = 1 - ty;
            if (y0 != mixed) {  // Rows between two source rows share them: mixed across once.
                mixed = y0;
                for (int x = 0; x < width; x++) {
                    int a = src[y0 * sw + x0[x]], b = src[y0 * sw + x1[x]], c = src[y1 * sw + x0[x]], d = src[y1 * sw + x1[x]];
                    float t = tx[x];
                    for (int k = 0, shift = 16; k < 3; k++, shift -= 8) {
                        top[k][x] = (float) ((a >> shift) & 255) * (1 - t) + (float) ((b >> shift) & 255) * t;
                        bottom[k][x] = (float) ((c >> shift) & 255) * (1 - t) + (float) ((d >> shift) & 255) * t;
                    }
                }
            }
            int[] dither = BAYER[y & 7];
            for (int x = 0; x < width; x++) {
                float n = (float) dither[x & 7] / 64.f;
                int red = (int) (top[0][x] * keep + bottom[0][x] * ty + n);
                int green = (int) (top[1][x] * keep + bottom[1][x] * ty + n);
                int blue = (int) (top[2][x] * keep + bottom[2][x] * ty + n);
                row[x] = 0xff000000 | (red > 255 ? 255 : red) << 16 | (green > 255 ? 255 : green) << 8 | (blue > 255 ? 255 : blue);
            }
            rows.row(y, row);
        }
    }
}

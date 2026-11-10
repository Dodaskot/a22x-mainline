// SPDX-License-Identifier: MIT
package org.a22x.installer;

import android.content.res.Resources;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.PixelFormat;
import android.graphics.drawable.Drawable;

/** The recovery's backdrop behind every page (2026-11-10): the theme's small soft image (the very
 *  pixels of the recovery's theme_assets.h, exported by ui/tools/export_backdrops.py) stretched to
 *  the screen by BackdropMath, the recovery's canvas_backdrop(). Made once per theme and size. */
final class Backdrop {
    private static Bitmap cached;
    private static int cachedId, cachedWidth, cachedHeight;

    private Backdrop() {
    }

    /** The backdrop of drawable ID at WIDTH x HEIGHT; the last one is kept for the next page. */
    static synchronized Bitmap get(Resources resources, int id, int width, int height) {
        if (cached != null && cachedId == id && cachedWidth == width && cachedHeight == height) return cached;
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inScaled = false;  // The source pixels as they are.
        Bitmap source = BitmapFactory.decodeResource(resources, id, options);
        int sw = source.getWidth(), sh = source.getHeight();
        int[] src = new int[sw * sh];
        source.getPixels(src, 0, sw, 0, 0, sw, sh);
        source.recycle();
        final Bitmap out = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        BackdropMath.stretch(src, sw, sh, width, height, (y, pixels) -> out.setPixels(pixels, 0, width, 0, y, width, 1));
        cached = out;
        cachedId = id;
        cachedWidth = width;
        cachedHeight = height;
        return out;
    }

    /** The window's backdrop for a view that starts OFFSET pixels below the window's top: during
     *  a theme change the new page covers the old one with the new backdrop, line for line. */
    static final class Shifted extends Drawable {
        private final Bitmap bitmap;
        private final int offset;

        Shifted(Bitmap bitmap, int offset) {
            this.bitmap = bitmap;
            this.offset = offset;
        }

        @Override
        public void draw(Canvas canvas) {
            canvas.drawBitmap(bitmap, 0, -offset, null);
        }

        @Override
        public void setAlpha(int alpha) {
        }

        @Override
        public void setColorFilter(ColorFilter filter) {
        }

        @Override
        public int getOpacity() {
            return PixelFormat.OPAQUE;
        }
    }
}

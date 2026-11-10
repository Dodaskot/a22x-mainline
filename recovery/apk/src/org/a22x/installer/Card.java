// SPDX-License-Identifier: MIT
package org.a22x.installer;

import android.graphics.Bitmap;
import android.graphics.BitmapShader;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Region;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;
import android.os.Build;

/** A surface drawn the way the recovery draws its own (2026-11-10, recovery_ui.c rounded(),
 *  bento_tile() and the hero): a soft shadow around it in the theme's shadow colour and strength,
 *  a lighter edge (the theme's edge colour and edge alpha) under a fill that lets the backdrop
 *  through (surface alpha, "glass"), a fill from one colour to another top to bottom, soft white
 *  discs (veils) inside, and a dark veil while pressed. */
final class Card extends Drawable {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF box = new RectF();
    private final Path path = new Path();
    private final float radius;
    private int top, bottom, edge, shadow, pressedVeil;
    private float edgeWidth, shadowBlur, shadowDy;
    private float[] veils = new float[0];
    private boolean pressed;
    private Bitmap image;
    private final Matrix matrix = new Matrix();
    private final RectF imageBox = new RectF();

    Card(float radius, int fill) {
        this.radius = radius;
        this.top = fill;
        this.bottom = fill;
    }

    /** From TOP to BOTTOM, as the recovery's gradient(). */
    Card gradient(int top, int bottom) {
        this.top = top;
        this.bottom = bottom;
        return this;
    }

    /** The lighter edge (with its alpha) WIDTH wide: drawn whole under the fill, as the recovery does. */
    Card edge(int color, float width) {
        edge = color;
        edgeWidth = width;
        return this;
    }

    /** COLOR's alpha is the strength; BLUR and DY in pixels. */
    Card shadow(int color, float blur, float dy) {
        shadow = color;
        shadowBlur = blur;
        shadowDy = dy;
        return this;
    }

    /** A white disc at (X, Y) of the width and height with radius R of the width, ALPHA 0-255. */
    Card veil(float x, float y, float r, int alpha) {
        float[] more = new float[veils.length + 4];
        System.arraycopy(veils, 0, more, 0, veils.length);
        more[veils.length] = x;
        more[veils.length + 1] = y;
        more[veils.length + 2] = r;
        more[veils.length + 3] = alpha;
        veils = more;
        return this;
    }

    /** The fill from a small image stretched over the card (a theme's backdrop on its card). */
    Card image(Bitmap bitmap) {
        image = bitmap;
        return this;
    }

    /** The black veil's alpha while the view is pressed (the recovery: 18 on tiles, 30 on the hero). */
    Card pressedVeil(int alpha) {
        pressedVeil = alpha;
        return this;
    }

    @Override
    public void draw(Canvas canvas) {
        Rect b = getBounds();
        box.set(b);
        path.reset();
        path.addRoundRect(box, radius, radius, Path.Direction.CW);
        // The shadow only around the card, never under its glass. Hardware canvases draw shadow
        // layers of shapes from Android 9; earlier the card simply has none.
        if ((shadow >>> 24) != 0 && Build.VERSION.SDK_INT >= 28) {
            canvas.save();
            canvas.clipPath(path, Region.Op.DIFFERENCE);
            paint.setShader(null);
            paint.setColor(0xff000000 | (shadow & 0xffffff));
            paint.setShadowLayer(shadowBlur, 0, shadowDy, shadow);
            canvas.drawRoundRect(box, radius, radius, paint);
            paint.clearShadowLayer();
            canvas.restore();
        }
        float r = radius;
        if ((edge >>> 24) != 0) {
            paint.setShader(null);
            paint.setColor(edge);
            canvas.drawRoundRect(box, r, r, paint);
            box.inset(edgeWidth, edgeWidth);
            r = Math.max(0, r - edgeWidth);
        }
        if (image != null) {
            BitmapShader shader = new BitmapShader(image, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP);
            imageBox.set(0, 0, image.getWidth(), image.getHeight());
            matrix.setRectToRect(imageBox, box, Matrix.ScaleToFit.FILL);
            shader.setLocalMatrix(matrix);
            paint.setColor(0xffffffff);
            paint.setFilterBitmap(true);
            paint.setShader(shader);
        } else if (top != bottom) {
            paint.setColor(0xffffffff);
            paint.setShader(new LinearGradient(0, box.top, 0, box.bottom, top, bottom, Shader.TileMode.CLAMP));
        } else {
            paint.setShader(null);
            paint.setColor(top);
        }
        canvas.drawRoundRect(box, r, r, paint);
        paint.setShader(null);
        if (veils.length > 0) {
            canvas.save();
            canvas.clipPath(path);
            for (int i = 0; i < veils.length; i += 4) {
                paint.setColor(((int) veils[i + 3]) << 24 | 0xffffff);
                canvas.drawCircle(b.left + veils[i] * b.width(), b.top + veils[i + 1] * b.height(), veils[i + 2] * b.width(), paint);
            }
            canvas.restore();
        }
        if (pressed && pressedVeil > 0) {
            box.set(b);
            paint.setColor(pressedVeil << 24);
            canvas.drawRoundRect(box, radius, radius, paint);
        }
    }

    @Override
    public boolean isStateful() {
        return pressedVeil > 0;
    }

    @Override
    protected boolean onStateChange(int[] state) {
        boolean now = false;
        for (int s : state) if (s == android.R.attr.state_pressed) now = true;
        if (now == pressed) return false;
        pressed = now;
        invalidateSelf();
        return true;
    }

    @Override
    public void setAlpha(int alpha) {
        paint.setAlpha(alpha);
    }

    @Override
    public void setColorFilter(ColorFilter filter) {
        paint.setColorFilter(filter);
    }

    @Override
    public int getOpacity() {
        return PixelFormat.TRANSLUCENT;
    }
}

// SPDX-License-Identifier: MIT
package org.a22x.installer;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.os.Vibrator;
import android.view.MotionEvent;
import android.view.View;
import android.view.animation.LinearInterpolator;

/** A button that acts only after it is held, filling from the left while held, as the recovery's
 *  hold buttons do (2026-11-10). Letting go early empties it again. */
final class HoldButton extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF box = new RectF();
    private String label;
    private final long holdMs;
    private final Runnable action;
    private final int fill, ink, disabledFill, disabledInk;
    private float progress;
    private ValueAnimator animator;
    private boolean done;

    HoldButton(Context context, String label, long holdMs, int fill, int ink, int disabledFill, int disabledInk, Runnable action) {
        super(context);
        this.label = label;
        this.holdMs = holdMs;
        this.fill = fill;
        this.ink = ink;
        this.disabledFill = disabledFill;
        this.disabledInk = disabledInk;
        this.action = action;
        paint.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        setClickable(true);
        setContentDescription(label);  // The drawn label, for TalkBack.
    }

    void setLabel(String text) {
        label = text;
        setContentDescription(text);
        invalidate();
    }

    @Override
    public void setEnabled(boolean enabled) {
        super.setEnabled(enabled);
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float radius = getHeight() / 2f;
        box.set(0, 0, getWidth(), getHeight());
        paint.setColor(isEnabled() ? fill : disabledFill);
        canvas.drawRoundRect(box, radius, radius, paint);
        if (progress > 0) {
            paint.setColor(0x40ffffff);
            box.set(0, 0, Math.max(getHeight(), getWidth() * progress), getHeight());
            canvas.drawRoundRect(box, radius, radius, paint);
        }
        paint.setColor(isEnabled() ? ink : disabledInk);
        paint.setTextSize(getHeight() * 0.27f);
        float width = paint.measureText(label);
        Paint.FontMetrics m = paint.getFontMetrics();
        canvas.drawText(label, (getWidth() - width) / 2f, (getHeight() - m.ascent - m.descent) / 2f, paint);
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        if (!isEnabled()) return false;
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                start();
                return true;
            case MotionEvent.ACTION_MOVE:
                if (event.getX() < 0 || event.getY() < 0 || event.getX() > getWidth() || event.getY() > getHeight()) release();
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                release();
                return true;
            default:
                return true;
        }
    }

    private void start() {
        if (animator != null) animator.cancel();
        done = false;
        animator = ValueAnimator.ofFloat(progress, 1f);
        animator.setDuration((long) (holdMs * (1f - progress)));
        animator.setInterpolator(new LinearInterpolator());
        animator.addUpdateListener(a -> {
            progress = (Float) a.getAnimatedValue();
            invalidate();
        });
        animator.addListener(new AnimatorListenerAdapter() {
            private boolean cancelled;

            @Override
            public void onAnimationCancel(Animator a) {
                cancelled = true;
            }

            @Override
            public void onAnimationEnd(Animator a) {
                if (cancelled || done) return;
                done = true;
                try {
                    Vibrator vibrator = (Vibrator) getContext().getSystemService(Context.VIBRATOR_SERVICE);
                    if (vibrator != null) vibrator.vibrate(40);
                } catch (RuntimeException ignored) {
                    // No vibration, the action still runs.
                }
                progress = 0;
                invalidate();
                action.run();
            }
        });
        animator.start();
    }

    private void release() {
        if (animator != null && !done) {
            animator.cancel();
            ValueAnimator back = ValueAnimator.ofFloat(progress, 0f);
            back.setDuration(180);
            back.addUpdateListener(a -> {
                progress = (Float) a.getAnimatedValue();
                invalidate();
            });
            back.start();
        }
        animator = null;
    }

    static View.OnTouchListener none() {
        return (v, e) -> false;
    }
}

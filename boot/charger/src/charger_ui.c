/* SPDX-License-Identifier: MIT */
#define _POSIX_C_SOURCE 200809L
#include "charger_ui.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/fb.h>
#include <linux/kd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "A22x framebuffer code requires a little-endian target"
#endif

#define UI_MAP_LIMIT (128U * 1024U * 1024U)
#define UI_BACKLIGHT_BASE "/sys/class/backlight/"
#define UI_BACKLIGHT_NAME "14013000.dsi-ml.0"
#define UI_BACKLIGHT_PREFIX "14013000.dsi-ml."

struct ui_canvas {
    uint8_t *memory;
    size_t length;
    unsigned int width, height, stride, xoffset, yoffset;
    unsigned int red, green, blue, alpha;
    int has_alpha;
};

struct ui_state {
    int fb_fd, tty_fd, brightness_fd;
    int old_mode, old_brightness, level;
    int mode_changed, brightness_changed, ready, visible;
    int last_capacity, last_connected, painted;
    void *mapping;
    size_t mapping_length;
    struct ui_canvas canvas;
};

static struct ui_state ui = {
    .fb_fd = -1, .tty_fd = -1, .brightness_fd = -1,
    .old_mode = -1, .old_brightness = -1
};

static int ui_rgb_field_ok(const struct fb_bitfield *field)
{
    return field->length == 8 && !field->msb_right &&
           field->offset <= 24 && !(field->offset % 8);
}

/* Pure validation; no address is dereferenced and no I/O is performed. */
static int ui_geometry(const struct fb_var_screeninfo *v,
                       const struct fb_fix_screeninfo *f,
                       struct ui_canvas *canvas)
{
    uint64_t required;

    if (f->type != FB_TYPE_PACKED_PIXELS || f->visual != FB_VISUAL_TRUECOLOR ||
        v->bits_per_pixel != 32 || v->grayscale || v->nonstd ||
        v->xres < 240 || v->xres > 4096 ||
        v->yres < 320 || v->yres > 8192 ||
        !v->xres_virtual || !v->yres_virtual ||
        (uint64_t)v->xoffset + v->xres > v->xres_virtual ||
        (uint64_t)v->yoffset + v->yres > v->yres_virtual ||
        (uint64_t)v->xres_virtual * 4 > f->line_length ||
        (f->line_length % 4) || !f->smem_len || f->smem_len > UI_MAP_LIMIT)
        return -1;
    required = (uint64_t)f->line_length * v->yres_virtual;
    if (required > f->smem_len)
        return -1;
    if (!ui_rgb_field_ok(&v->red) || !ui_rgb_field_ok(&v->green) ||
        !ui_rgb_field_ok(&v->blue) ||
        v->red.offset == v->green.offset ||
        v->red.offset == v->blue.offset ||
        v->green.offset == v->blue.offset)
        return -1;
    if (v->transp.length &&
        (!ui_rgb_field_ok(&v->transp) ||
         v->transp.offset == v->red.offset ||
         v->transp.offset == v->green.offset ||
         v->transp.offset == v->blue.offset))
        return -1;
    if (!v->transp.length && v->transp.msb_right)
        return -1;

    memset(canvas, 0, sizeof(*canvas));
    canvas->length = f->smem_len;
    canvas->width = v->xres;
    canvas->height = v->yres;
    canvas->stride = f->line_length;
    canvas->xoffset = v->xoffset;
    canvas->yoffset = v->yoffset;
    canvas->red = v->red.offset;
    canvas->green = v->green.offset;
    canvas->blue = v->blue.offset;
    canvas->alpha = v->transp.offset;
    canvas->has_alpha = v->transp.length == 8;
    return 0;
}

static uint32_t ui_pixel(const struct ui_canvas *canvas,
                         unsigned int red, unsigned int green, unsigned int blue)
{
    uint32_t pixel = (uint32_t)red << canvas->red |
                     (uint32_t)green << canvas->green |
                     (uint32_t)blue << canvas->blue;
    if (canvas->has_alpha)
        pixel |= UINT32_C(255) << canvas->alpha;
    return pixel;
}

/* Clip both axes before calculating byte offsets; preserve virtual/padding rows. */
static void ui_rect(struct ui_canvas *canvas, int x, int y, int w, int h,
                    uint32_t pixel)
{
    int64_t x0 = x, y0 = y, x1 = (int64_t)x + w, y1 = (int64_t)y + h;
    if (w <= 0 || h <= 0)
        return;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > canvas->width) x1 = canvas->width;
    if (y1 > canvas->height) y1 = canvas->height;
    if (x0 >= x1 || y0 >= y1)
        return;
    for (int64_t row = y0; row < y1; row++) {
        size_t start = ((size_t)canvas->yoffset + (size_t)row) * canvas->stride +
                       ((size_t)canvas->xoffset + (size_t)x0) * 4;
        size_t count = (size_t)(x1 - x0);
        if (start > canvas->length || count > (canvas->length - start) / 4)
            return;
        for (size_t col = 0; col < count; col++)
            memcpy(canvas->memory + start + col * 4, &pixel, sizeof(pixel));
    }
}

static void ui_round_rect(struct ui_canvas *canvas, int x, int y, int w, int h,
                          int radius, uint32_t pixel)
{
    if (w <= 0 || h <= 0)
        return;
    if (radius < 0) radius = 0;
    if (radius > w / 2) radius = w / 2;
    if (radius > h / 2) radius = h / 2;
    for (int row = 0; row < h; row++) {
        int inset = 0;
        int edge = row < h / 2 ? row : h - 1 - row;
        if (edge < radius) {
            int dy = radius - 1 - edge;
            int dx = radius;
            while (dx > 0 && dx * dx + dy * dy > radius * radius)
                dx--;
            inset = radius - dx;
        }
        ui_rect(canvas, x + inset, y + row, w - 2 * inset, 1, pixel);
    }
}

/* Five-by-seven glyphs keep the binary independent of fonts/libraries. */
static const uint8_t ui_glyphs[12][7] = {
    {14, 27, 27, 27, 27, 27, 14},
    { 6, 14,  6,  6,  6,  6, 15},
    {14, 27,  3,  6, 12, 24, 31},
    {30,  3,  3, 14,  3,  3, 30},
    { 3,  7, 15, 27, 31,  3,  3},
    {31, 24, 24, 30,  3,  3, 30},
    {14, 24, 24, 30, 27, 27, 14},
    {31,  3,  3,  6, 12, 12, 12},
    {14, 27, 27, 14, 27, 27, 14},
    {14, 27, 27, 15,  3,  3, 14},
    {25, 27,  2,  4,  8, 27, 19}, /* % */
    { 0,  0,  0, 31,  0,  0,  0}  /* - */
};

static void ui_text(struct ui_canvas *canvas, const char *text, int cx, int y,
                    int scale, uint32_t pixel)
{
    int total = ((int)strlen(text) * 6 - 1) * scale;
    int x = cx - total / 2;
    for (; *text; text++, x += 6 * scale) {
        int index = *text >= '0' && *text <= '9' ? *text - '0' :
                    *text == '%' ? 10 : 11;
        for (int row = 0; row < 7; row++)
            for (int col = 0; col < 5; col++)
                if (ui_glyphs[index][row] & (1U << (4 - col)))
                    ui_rect(canvas, x + col * scale, y + row * scale,
                            scale, scale, pixel);
    }
}

/* A compact bolt, with no text labels or controls on the charging screen. */
static void ui_bolt(struct ui_canvas *canvas, int cx, int top, int scale,
                    uint32_t pixel)
{
    static const uint8_t shape[9] = { 3, 6, 12, 24, 31, 6, 12, 8, 16 };
    for (int row = 0; row < 9; row++)
        for (int col = 0; col < 5; col++)
            if (shape[row] & (1U << (4 - col)))
                ui_rect(canvas, cx - 5 * scale / 2 + col * scale,
                        top + row * scale, scale, scale, pixel);
}

/* Pure framebuffer drawing, shared with in-memory host tests. */
static void ui_draw(struct ui_canvas *canvas, int capacity, int connected)
{
    int known = capacity >= 0 && capacity <= 100;
    int bw = (int)canvas->width * 30 / 100;
    if (bw < 120) bw = 120;
    if (bw > 360) bw = 360;
    int bh = bw * 8 / 5;
    int x = ((int)canvas->width - bw) / 2;
    int y = ((int)canvas->height - bh - bw * 4 / 5) / 2;
    if (y < 32) y = 32;
    int edge = bw / 28;
    if (edge < 5) edge = 5;
    int radius = bw / 12;
    int inset = 2 * edge + 3;
    int scale = bw / 24;
    if (scale < 3) scale = 3;
    uint32_t black = ui_pixel(canvas, 0, 0, 0);
    uint32_t white = ui_pixel(canvas, 224, 231, 239);
    uint32_t accent = connected == 1 ? ui_pixel(canvas, 88, 217, 157) :
                      connected == 0 ? ui_pixel(canvas, 242, 185, 88) :
                                       ui_pixel(canvas, 144, 169, 194);
    char value[8];

    ui_rect(canvas, 0, 0, (int)canvas->width, (int)canvas->height, black);
    ui_round_rect(canvas, x + bw * 35 / 100, y - edge * 2,
                  bw * 30 / 100, edge * 3, edge, white);
    ui_round_rect(canvas, x, y, bw, bh, radius, white);
    ui_round_rect(canvas, x + edge, y + edge, bw - 2 * edge, bh - 2 * edge,
                  radius - edge, black);
    if (known) {
        int filled = (bh - 2 * inset) * capacity / 100;
        if (filled > 0)
            ui_round_rect(canvas, x + inset, y + bh - inset - filled,
                          bw - 2 * inset, filled, radius, accent);
        (void)snprintf(value, sizeof(value), "%d%%", capacity);
    } else {
        ui_rect(canvas, x + bw * 3 / 10, y + bh * 48 / 100,
                bw * 4 / 10, edge, accent);
        memcpy(value, "--%", 4);
    }
    ui_text(canvas, value, (int)canvas->width / 2, y + bh + bw * 18 / 100,
            scale, white);
    if (connected == 1)
        ui_bolt(canvas, (int)canvas->width / 2, y + bh + bw * 60 / 100,
                scale / 2 > 0 ? scale / 2 : 1, accent);
}

static int ui_read_number(int fd, int *value)
{
    char buffer[32], *end;
    ssize_t count = pread(fd, buffer, sizeof(buffer) - 1, 0);
    if (count <= 0) {
        if (!count) errno = EIO;
        return -1;
    }
    buffer[count] = '\0';
    errno = 0;
    long parsed = strtol(buffer, &end, 10);
    if (errno || end == buffer || parsed < 0 || parsed > 65535) {
        errno = EINVAL;
        return -1;
    }
    while (*end == ' ' || *end == '\t' || *end == '\n') end++;
    if (*end) { errno = EINVAL; return -1; }
    *value = (int)parsed;
    return 0;
}

static int ui_write_number(int fd, int value)
{
    char buffer[32];
    int length = snprintf(buffer, sizeof(buffer), "%d\n", value);
    ssize_t count;
    do {
        count = pwrite(fd, buffer, (size_t)length, 0);
    } while (count < 0 && errno == EINTR);
    if (count != length) {
        if (count >= 0) errno = EIO;
        return -1;
    }
    return 0;
}

static int ui_backlight_path(char *destination, size_t capacity)
{
    const char *exact = UI_BACKLIGHT_BASE UI_BACKLIGHT_NAME;
    char candidate[PATH_MAX];
    int count = 0;
    if (access(exact, F_OK) == 0) {
        if (snprintf(destination, capacity, "%s", exact) >= (int)capacity) {
            errno = ENAMETOOLONG;
            return -1;
        }
        return 0;
    }
    DIR *directory = opendir(UI_BACKLIGHT_BASE);
    if (!directory)
        return -1;
    struct dirent *entry;
    while ((entry = readdir(directory))) {
        if (strncmp(entry->d_name, UI_BACKLIGHT_PREFIX,
                    sizeof(UI_BACKLIGHT_PREFIX) - 1))
            continue;
        count++;
        if (snprintf(candidate, sizeof(candidate), "%s%s",
                     UI_BACKLIGHT_BASE, entry->d_name) >= (int)sizeof(candidate)) {
            closedir(directory);
            errno = ENAMETOOLONG;
            return -1;
        }
    }
    closedir(directory);
    if (count != 1) {
        errno = count ? EEXIST : ENODEV;
        return -1;
    }
    if (snprintf(destination, capacity, "%s", candidate) >= (int)capacity) {
        errno = ENAMETOOLONG;
        return -1;
    }
    return 0;
}

int charger_ui_open(void)
{
    struct fb_var_screeninfo variable;
    struct fb_fix_screeninfo fixed;
    char backlight[PATH_MAX], path[PATH_MAX];
    int maximum_fd = -1, maximum, saved;
    if (ui.ready)
        return 0;

    memset(&variable, 0, sizeof(variable));
    memset(&fixed, 0, sizeof(fixed));
    ui.fb_fd = open("/dev/fb0", O_RDWR | O_CLOEXEC | O_NOCTTY);
    if (ui.fb_fd < 0)
        goto fail;
    if (ioctl(ui.fb_fd, FBIOGET_VSCREENINFO, &variable) < 0 ||
        ioctl(ui.fb_fd, FBIOGET_FSCREENINFO, &fixed) < 0)
        goto fail;
    if (strncmp(fixed.id, "mediatekdrmfb", sizeof(fixed.id)) ||
        ui_geometry(&variable, &fixed, &ui.canvas) < 0) {
        errno = ENODEV;
        goto fail;
    }
    ui.mapping_length = fixed.smem_len;
    ui.mapping = mmap(NULL, ui.mapping_length, PROT_READ | PROT_WRITE,
                      MAP_SHARED, ui.fb_fd, 0);
    if (ui.mapping == MAP_FAILED) {
        ui.mapping = NULL;
        goto fail;
    }
    ui.canvas.memory = ui.mapping;

    if (ui_backlight_path(backlight, sizeof(backlight)) < 0)
        goto fail;
    if (snprintf(path, sizeof(path), "%s/brightness", backlight) >= (int)sizeof(path)) {
        errno = ENAMETOOLONG;
        goto fail;
    }
    ui.brightness_fd = open(path, O_RDWR | O_CLOEXEC);
    if (ui.brightness_fd < 0 || ui_read_number(ui.brightness_fd, &ui.old_brightness) < 0)
        goto fail;
    if (snprintf(path, sizeof(path), "%s/max_brightness", backlight) >= (int)sizeof(path)) {
        errno = ENAMETOOLONG;
        goto fail;
    }
    maximum_fd = open(path, O_RDONLY | O_CLOEXEC);
    if (maximum_fd < 0 || ui_read_number(maximum_fd, &maximum) < 0)
        goto fail;
    close(maximum_fd);
    maximum_fd = -1;
    if (maximum <= 0 || ui.old_brightness > maximum) {
        errno = EINVAL;
        goto fail;
    }
    ui.level = maximum * 8 / 100;
    if (ui.level < 1) ui.level = 1;

    /* All framebuffer/backlight checks precede changing the console mode. */
    ui.tty_fd = open("/dev/tty0", O_RDWR | O_NOCTTY | O_CLOEXEC);
    if (ui.tty_fd < 0 || ioctl(ui.tty_fd, KDGETMODE, &ui.old_mode) < 0)
        goto fail;
    if (ioctl(ui.tty_fd, KDSETMODE, KD_GRAPHICS) < 0)
        goto fail;
    ui.mode_changed = 1;
    /* Hide any old frame, then clear it before the first visible render. */
    ui.brightness_changed = 1;
    if (ui_write_number(ui.brightness_fd, 0) < 0)
        goto fail;
    ui_rect(&ui.canvas, 0, 0, (int)ui.canvas.width, (int)ui.canvas.height,
            ui_pixel(&ui.canvas, 0, 0, 0));
    ui.ready = 1;
    ui.visible = 0;
    ui.painted = 0;
    errno = 0;
    return 0;
fail:
    saved = errno ? errno : EIO;
    if (maximum_fd >= 0) close(maximum_fd);
    charger_ui_close();
    errno = saved;
    return -1;
}

int charger_ui_render(int capacity, int connected, int screen_on)
{
    errno = 0;
    if (!ui.ready) { errno = ENODEV; return -1; }
    if (!screen_on) {
        if (ui.visible && ui_write_number(ui.brightness_fd, 0) < 0)
            return -1;
        ui.visible = 0;
        return 0;
    }
    if (capacity < 0 || capacity > 100) capacity = -1;
    if (connected != 0 && connected != 1) connected = -1;
    if (!ui.painted || ui.last_capacity != capacity || ui.last_connected != connected) {
        ui_draw(&ui.canvas, capacity, connected);
        ui.last_capacity = capacity;
        ui.last_connected = connected;
        ui.painted = 1;
    }
    if (!ui.visible) {
        if (ui_write_number(ui.brightness_fd, ui.level) < 0)
            return -1;
        ui.visible = 1;
    }
    return 0;
}

void charger_ui_close(void)
{
    int saved = errno;
    if (ui.brightness_changed && ui.brightness_fd >= 0)
        (void)ui_write_number(ui.brightness_fd, 0);
    if (ui.mode_changed && ui.tty_fd >= 0 && ui.old_mode >= 0)
        (void)ioctl(ui.tty_fd, KDSETMODE, ui.old_mode);
    if (ui.tty_fd >= 0) close(ui.tty_fd);
    if (ui.mapping) munmap(ui.mapping, ui.mapping_length);
    if (ui.fb_fd >= 0) close(ui.fb_fd);
    if (ui.brightness_changed && ui.brightness_fd >= 0 && ui.old_brightness >= 0)
        (void)ui_write_number(ui.brightness_fd, ui.old_brightness);
    if (ui.brightness_fd >= 0) close(ui.brightness_fd);
    memset(&ui, 0, sizeof(ui));
    ui.fb_fd = ui.tty_fd = ui.brightness_fd = -1;
    ui.old_mode = ui.old_brightness = -1;
    errno = saved;
}

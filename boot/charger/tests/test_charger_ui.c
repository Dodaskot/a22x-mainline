/* SPDX-License-Identifier: MIT */
/* Host-only pure UI tests: no calls to charger_ui_open/render/close or /dev. */
#include "../src/charger_ui.c"
#include <assert.h>

static void geometry_fixture(struct fb_var_screeninfo *v,
                             struct fb_fix_screeninfo *f)
{
    memset(v, 0, sizeof(*v));
    memset(f, 0, sizeof(*f));
    v->xres = v->xres_virtual = 1080;
    v->yres = v->yres_virtual = 2408;
    v->bits_per_pixel = 32;
    v->red.offset = 16; v->red.length = 8;
    v->green.offset = 8; v->green.length = 8;
    v->blue.length = 8;
    f->type = FB_TYPE_PACKED_PIXELS;
    f->visual = FB_VISUAL_TRUECOLOR;
    f->line_length = 4320;
    f->smem_len = 10402560;
}

static void test_geometry(void)
{
    struct fb_var_screeninfo v;
    struct fb_fix_screeninfo f;
    struct ui_canvas c;
    geometry_fixture(&v, &f);
    assert(ui_geometry(&v, &f, &c) == 0);
    assert(c.width == 1080 && c.height == 2408 && c.stride == 4320);
    f.smem_len--;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); f.line_length = 4316;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); v.xoffset = UINT_MAX;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); v.xres_virtual = UINT_MAX;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); v.yres_virtual = UINT_MAX;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); v.bits_per_pixel = 16;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); v.red.offset = 8;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); v.red.offset = 7;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); v.red.msb_right = 1;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); v.transp.length = 8; v.transp.offset = 0;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); v.transp.length = 4; v.transp.offset = 24;
    assert(ui_geometry(&v, &f, &c) < 0);
    geometry_fixture(&v, &f); f.smem_len = UINT_MAX;
    assert(ui_geometry(&v, &f, &c) < 0);
}

static void test_pixels(void)
{
    struct fb_var_screeninfo v;
    struct fb_fix_screeninfo f;
    struct ui_canvas c;
    geometry_fixture(&v, &f);
    assert(ui_geometry(&v, &f, &c) == 0);
    assert(ui_pixel(&c, 255, 0, 0) == UINT32_C(0x00ff0000));
    assert(ui_pixel(&c, 0, 255, 0) == UINT32_C(0x0000ff00));
    assert(ui_pixel(&c, 0, 0, 255) == UINT32_C(0x000000ff));
    v.transp.offset = 24; v.transp.length = 8;
    assert(ui_geometry(&v, &f, &c) == 0);
    assert(ui_pixel(&c, 7, 8, 9) == UINT32_C(0xff070809));
    v.red.offset = 0; v.blue.offset = 16;
    assert(ui_geometry(&v, &f, &c) == 0);
    assert(ui_pixel(&c, 7, 8, 9) == UINT32_C(0xff090807));
}

static void test_clipping(void)
{
    uint8_t memory[16 + 400 + 16];
    struct ui_canvas c = {
        .memory = memory + 16, .length = 400,
        .width = 10, .height = 10, .stride = 40,
        .red = 16, .green = 8, .blue = 0
    };
    memset(memory, 0xa5, sizeof(memory));
    ui_rect(&c, -100, -100, 1000, 1000, UINT32_C(0x00070809));
    for (unsigned int i = 0; i < 16; i++) {
        assert(memory[i] == 0xa5);
        assert(memory[416 + i] == 0xa5);
    }
    for (unsigned int i = 16; i < 416; i += 4) {
        uint32_t pixel;
        memcpy(&pixel, memory + i, 4);
        assert(pixel == UINT32_C(0x00070809));
    }
    uint8_t before[sizeof(memory)];
    memcpy(before, memory, sizeof(memory));
    ui_rect(&c, INT_MAX - 1, INT_MAX - 1, INT_MAX, INT_MAX, 0);
    ui_rect(&c, INT_MIN, INT_MIN, 1, 1, 0);
    ui_rect(&c, 1, 1, -1, -1, 0);
    assert(!memcmp(before, memory, sizeof(memory)));
}

static uint64_t visible_hash(const struct ui_canvas *c)
{
    uint64_t hash = UINT64_C(1469598103934665603);
    for (unsigned int y = 0; y < c->height; y++) {
        size_t start = ((size_t)c->yoffset + y) * c->stride + c->xoffset * 4;
        for (unsigned int x = 0; x < c->width * 4; x++) {
            hash ^= c->memory[start + x];
            hash *= UINT64_C(1099511628211);
        }
    }
    return hash;
}

static void test_render_bounds_and_states(void)
{
    struct fb_var_screeninfo v;
    struct fb_fix_screeninfo f;
    struct ui_canvas c;
    geometry_fixture(&v, &f);
    v.xoffset = 8; v.yoffset = 2;
    v.xres_virtual += 16; v.yres_virtual += 6;
    f.line_length = v.xres_virtual * 4;
    f.smem_len = f.line_length * v.yres_virtual;
    assert(ui_geometry(&v, &f, &c) == 0);
    uint8_t *allocation = malloc((size_t)f.smem_len + 32);
    assert(allocation);
    c.memory = allocation + 16;
    int capacities[] = {-1, 0, 1, 50, 99, 100, 101};
    uint64_t unknown_hash = 0, zero_hash = 0, connected_hash = 0;
    for (size_t n = 0; n < sizeof(capacities) / sizeof(capacities[0]); n++) {
        memset(allocation, 0xa5, (size_t)f.smem_len + 32);
        ui_draw(&c, capacities[n], 1);
        for (unsigned int y = 0; y < v.yres_virtual; y++)
            for (unsigned int x = 0; x < v.xres_virtual; x++) {
                if (y >= v.yoffset && y < v.yoffset + v.yres &&
                    x >= v.xoffset && x < v.xoffset + v.xres)
                    continue;
                size_t offset = (size_t)y * c.stride + x * 4;
                for (unsigned int b = 0; b < 4; b++)
                    assert(c.memory[offset + b] == 0xa5);
            }
        for (unsigned int b = 0; b < 16; b++) {
            assert(allocation[b] == 0xa5);
            assert(allocation[16 + f.smem_len + b] == 0xa5);
        }
        uint64_t hash = visible_hash(&c);
        if (capacities[n] == -1) unknown_hash = hash;
        if (capacities[n] == 0) zero_hash = hash;
        if (capacities[n] == 50) connected_hash = hash;
        if (capacities[n] == 101) assert(hash == unknown_hash);
    }
    assert(unknown_hash != zero_hash);
    ui_draw(&c, 50, 0);
    uint64_t disconnected_hash = visible_hash(&c);
    ui_draw(&c, 50, -1);
    uint64_t unknown_power_hash = visible_hash(&c);
    assert(connected_hash != disconnected_hash && connected_hash != unknown_power_hash);
    assert(disconnected_hash != unknown_power_hash);
    free(allocation);
}

static void preview(const char *path)
{
    struct ui_canvas c = {
        .width = 360, .height = 803, .stride = 1440,
        .length = 1440 * 803, .red = 16, .green = 8, .blue = 0
    };
    c.memory = calloc(1, c.length);
    assert(c.memory);
    ui_draw(&c, 73, 1);
    FILE *out = fopen(path, "wb");
    assert(out);
    assert(fprintf(out, "P6\n%u %u\n255\n", c.width, c.height) > 0);
    for (unsigned int y = 0; y < c.height; y++)
        for (unsigned int x = 0; x < c.width; x++) {
            const uint8_t *pixel = c.memory + (size_t)y * c.stride + x * 4;
            uint8_t rgb[3] = {pixel[2], pixel[1], pixel[0]};
            assert(fwrite(rgb, 1, 3, out) == 3);
        }
    assert(fclose(out) == 0);
    free(c.memory);
}

int main(int argc, char **argv)
{
    assert(argc == 1 || argc == 2);
    test_geometry();
    test_pixels();
    test_clipping();
    test_render_bounds_and_states();
    if (argc == 2) preview(argv[1]);
    puts("charger_ui: geometry, packing, clipping, padded render and state tests PASS");
    return 0;
}

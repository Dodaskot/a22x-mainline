/* SPDX-License-Identifier: MIT
 * The recovery's own canvas_backdrop() on a computer (2026-11-10): writes each theme's backdrop at
 * the recovery's native size (NW x NH, 0x00RRGGBB, little endian) for BackdropTest, which checks
 * that the app's BackdropMath gives the very same pixels. */
#include <stdio.h>
#include "native_canvas.h"

int main(int argc, char **argv) {
    static uint32_t out[(size_t)NW * NH];
    if (argc != 3) { fprintf(stderr, "usage: backdrop_ref DAYLIGHT_OUT AURORA_OUT\n"); return 2; }
    const uint8_t *sources[2] = {backdrop_daylight, backdrop_aurora};
    for (int n = 0; n < 2; n++) {
        canvas_backdrop(out, sources[n], BACKDROP_W, BACKDROP_H);
        FILE *f = fopen(argv[1 + n], "wb");
        if (!f || fwrite(out, sizeof(uint32_t), (size_t)NW * NH, f) != (size_t)NW * NH || fclose(f) != 0) { perror(argv[1 + n]); return 1; }
    }
    printf("%d %d\n", NW, NH);
    return 0;
}

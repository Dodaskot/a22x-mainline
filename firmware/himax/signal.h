/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HX_SIGNAL_H
#define HX_SIGNAL_H
#include <stddef.h>
#include <stdint.h>

#define HX_SIGNAL_ROWS 16u
#define HX_SIGNAL_COLS 36u
#define HX_SIGNAL_CELLS (HX_SIGNAL_ROWS * HX_SIGNAL_COLS)

enum hx_signal_result {
    HX_SIGNAL_OK = 0,
    HX_SIGNAL_INVALID = -1,
    HX_SIGNAL_BOUNDS = -2,
    HX_SIGNAL_ZERO_WEIGHT = -3
};

/* Arrays contain host-endian numeric values, not packed controller bytes.
 * Output is a packed 16x36 delta matrix; padding/border correction is separate.
 * Validate every mapping index before writing any output. Arrays must not overlap.
 */
int hx_signal_mapped_delta(int16_t *delta, size_t delta_count,
                           const uint16_t *raw, const uint16_t *baseline,
                           size_t raw_count, const uint16_t *mapping,
                           size_t mapping_count);

struct hx_signal_moment {
    uint32_t col_weight;
    uint32_t row_weight;
    uint32_t weight;
};

struct hx_signal_axes {
    uint16_t col_axis; /* 0..2407 before the separate report axis swap */
    uint16_t row_axis; /* 0..1079 before the separate report axis swap */
};

/* One padded-grid cell, coordinates row 0..17 and col 0..37. Negative
 * samples contribute zero; unsigned sums deliberately wrap at 32 bits.
 * Caller chooses core/outer record and owns clustering/visited state.
 */
int hx_signal_accumulate(struct hx_signal_moment *moment, int16_t sample,
                          unsigned row, unsigned col);

/* Pure positive-weight scale/clip/invert block. Not a contact detector.
 * Zero weight and invalid inversion flags fail without modifying output.
 */
int hx_signal_scale(struct hx_signal_axes *out,
                     const struct hx_signal_moment *moment,
                     unsigned invert_col, unsigned invert_row);
#endif

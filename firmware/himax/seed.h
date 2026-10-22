/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HX_SEED_H
#define HX_SEED_H
#include <stddef.h>
#include <stdint.h>

#define HX_SEED_ROWS 16u
#define HX_SEED_COLS 36u
#define HX_SEED_CELLS (HX_SEED_ROWS * HX_SEED_COLS)
#define HX_SEED_LIMIT 150u
#define HX_SEED_PADDED_CELLS ((HX_SEED_ROWS + 2u) * (HX_SEED_COLS + 2u))
#define HX_SEED_BELOW 253u
#define HX_SEED_ABOVE 254u

struct hx_seed {
    uint8_t row; /* one-based: 1..16 */
    uint8_t col; /* one-based: 1..36 */
};

struct hx_seed_result {
    uint16_t labels[HX_SEED_CELLS]; /* packed; no padded border policy */
    struct hx_seed seeds[HX_SEED_LIMIT];
    uint16_t count;
};

/* Ordinary full-matrix threshold classifier, not peak selection/flood fill.
 * Signal and thresholds are host-endian numeric arrays, without overlap with
 * output. Non-NULL cell_thresholds selects per-cell unsigned16 thresholds;
 * otherwise scalar_threshold applies. Comparison is strictly signal > T.
 * Every cell is labeled even after the row-major seed list reaches its cap.
 * Only seeds[0..count) are written; unused seed slots retain previous bytes.
 * Returns 0 on success, -1 on NULL required pointers, -2 on short arrays.
 * Validation failures leave output unchanged. Dense-frame exception handling,
 * counter/flag side effects and padded-edge correction are not implemented.
 */
int hx_seed_extract(struct hx_seed_result *out, const int16_t *signal,
                      size_t signal_count, uint16_t scalar_threshold,
                      const uint16_t *cell_thresholds, size_t threshold_count);

struct hx_seed_peak {
    uint8_t row;
    uint8_t col;
    uint16_t weight;
};

/* Pick the first largest positive-weight seed still labeled254. Weights use
 * an 18x38 padded grid; labels stay packed16x36. No flood fill or mutation.
 * Returns 0 for a peak, 1 for none, -1 for NULL pointers, -2 for invalid
 * bounds/count/coordinates. Failure/no peak leaves output unchanged.
 */
int hx_seed_pick(struct hx_seed_peak *out, const struct hx_seed_result *seeds,
                   const int16_t *padded_weights, size_t weight_count);
#endif

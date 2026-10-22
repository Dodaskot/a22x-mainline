/* SPDX-License-Identifier: GPL-2.0-only */
#include "seed.h"

int hx_seed_extract(struct hx_seed_result *out, const int16_t *signal,
                      size_t signal_count, uint16_t scalar_threshold,
                      const uint16_t *cell_thresholds, size_t threshold_count)
{
    size_t i;
    uint16_t count = 0;
    if (!out || !signal)
        return -1;
    if (signal_count < HX_SEED_CELLS ||
            (cell_thresholds && threshold_count < HX_SEED_CELLS))
        return -2;
    for (i = 0; i < HX_SEED_CELLS; ++i) {
        uint16_t threshold = cell_thresholds ? cell_thresholds[i] : scalar_threshold;
        if ((int32_t)signal[i] > (int32_t)threshold) {
            out->labels[i] = HX_SEED_ABOVE;
            if (count < HX_SEED_LIMIT) {
                out->seeds[count].row = (uint8_t)(i / HX_SEED_COLS + 1u);
                out->seeds[count].col = (uint8_t)(i % HX_SEED_COLS + 1u);
                ++count;
            }
        } else {
            out->labels[i] = HX_SEED_BELOW;
        }
    }
    out->count = count;
    return 0;
}

int hx_seed_pick(struct hx_seed_peak *out, const struct hx_seed_result *seeds,
                   const int16_t *padded_weights, size_t weight_count)
{
    size_t i;
    uint16_t best_weight = 0;
    struct hx_seed_peak best;
    if (!out || !seeds || !padded_weights)
        return -1;
    if (weight_count < HX_SEED_PADDED_CELLS || seeds->count > HX_SEED_LIMIT)
        return -2;
    for (i = 0; i < seeds->count; ++i)
        if (!seeds->seeds[i].row || seeds->seeds[i].row > HX_SEED_ROWS ||
                !seeds->seeds[i].col || seeds->seeds[i].col > HX_SEED_COLS)
            return -2;
    for (i = 0; i < seeds->count; ++i) {
        unsigned row = seeds->seeds[i].row;
        unsigned col = seeds->seeds[i].col;
        int32_t weight = padded_weights[row * (HX_SEED_COLS + 2u) + col];
        size_t label_index = (row - 1u) * HX_SEED_COLS + col - 1u;
        if (seeds->labels[label_index] == HX_SEED_ABOVE && weight > best_weight) {
            best.row = (uint8_t)row;
            best.col = (uint8_t)col;
            best.weight = best_weight = (uint16_t)weight;
        }
    }
    if (!best_weight)
        return 1;
    *out = best;
    return 0;
}

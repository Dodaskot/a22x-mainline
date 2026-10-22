/* SPDX-License-Identifier: GPL-2.0-only */
#include "signal.h"

int hx_signal_mapped_delta(int16_t *delta, size_t delta_count,
                           const uint16_t *raw, const uint16_t *baseline,
                           size_t raw_count, const uint16_t *mapping,
                           size_t mapping_count)
{
    size_t i;
    if (!delta || !raw || !baseline || !mapping)
        return HX_SIGNAL_INVALID;
    if (delta_count < HX_SIGNAL_CELLS || mapping_count < HX_SIGNAL_CELLS)
        return HX_SIGNAL_BOUNDS;
    for (i = 0; i < HX_SIGNAL_CELLS; ++i)
        if ((size_t)mapping[i] >= raw_count)
            return HX_SIGNAL_BOUNDS;
    for (i = 0; i < HX_SIGNAL_CELLS; ++i) {
        size_t index = mapping[i];
        uint32_t wrapped = ((uint32_t)raw[index] + UINT32_C(65536)
                            - (uint32_t)baseline[index]) & UINT32_C(65535);
        int32_t signed_delta = (int32_t)wrapped;
        if (wrapped >= UINT32_C(32768))
            signed_delta -= INT32_C(65536);
        /* signed_delta is provably within int16_t range. */
        delta[i] = (int16_t)signed_delta;
    }
    return HX_SIGNAL_OK;
}

int hx_signal_accumulate(struct hx_signal_moment *moment, int16_t sample,
                          unsigned row, unsigned col)
{
    uint32_t weight;
    if (!moment)
        return HX_SIGNAL_INVALID;
    if (row >= HX_SIGNAL_ROWS + 2u || col >= HX_SIGNAL_COLS + 2u)
        return HX_SIGNAL_BOUNDS;
    weight = sample > 0 ? (uint32_t)sample : UINT32_C(0);
    moment->col_weight += weight * (uint32_t)col;
    moment->row_weight += weight * (uint32_t)row;
    moment->weight += weight;
    return HX_SIGNAL_OK;
}

static uint16_t scale_axis(uint64_t numerator, uint32_t weight,
                           uint32_t maximum, unsigned invert)
{
    uint64_t quotient = numerator / weight;
    uint32_t coordinate;
    if (quotient <= UINT64_C(33))
        coordinate = 0;
    else {
        /* Stock stores the low 32 bits after subtraction before clipping.
         * Preserve that wrap even for synthetic, nonphysical large moments. */
        coordinate = (uint32_t)(quotient - UINT64_C(33));
        if (coordinate > maximum)
            coordinate = maximum;
    }
    if (invert)
        coordinate = maximum - coordinate;
    return (uint16_t)coordinate;
}

int hx_signal_scale(struct hx_signal_axes *out,
                     const struct hx_signal_moment *moment,
                     unsigned invert_col, unsigned invert_row)
{
    uint64_t col_numerator, row_numerator;
    struct hx_signal_axes result;
    if (!out || !moment || invert_col > 1u || invert_row > 1u)
        return HX_SIGNAL_INVALID;
    if (!moment->weight)
        return HX_SIGNAL_ZERO_WEIGHT;
    col_numerator = (uint64_t)moment->col_weight * UINT64_C(2408) / UINT64_C(36);
    row_numerator = ((uint64_t)moment->row_weight * UINT64_C(1080)) >> 4;
    result.col_axis = scale_axis(col_numerator, moment->weight, 2407u, invert_col);
    result.row_axis = scale_axis(row_numerator, moment->weight, 1079u, invert_row);
    *out = result;
    return HX_SIGNAL_OK;
}

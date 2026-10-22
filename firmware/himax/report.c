/* SPDX-License-Identifier: GPL-2.0-only */
#include "report.h"

static void finish_checksum(uint8_t *out)
{
    unsigned sum = 0;
    for (unsigned i = 0; i < HX_REPORT_BYTES - 1; ++i)
        sum += out[i];
    out[HX_REPORT_BYTES - 1] = (uint8_t)(0u - sum);
}

int hx_report_encode(uint8_t *out, size_t capacity,
                     const struct hx_contact contacts[HX_CONTACT_SLOTS],
                     unsigned active_count, uint16_t metadata,
                     unsigned tag, int swap_xy)
{
    if (!out || !contacts || capacity < HX_REPORT_BYTES ||
        active_count > HX_CONTACT_SLOTS || tag > 15)
        return -1;
    for (unsigned i = 0; i < HX_CONTACT_SLOTS; ++i) {
        uint16_t x = swap_xy ? contacts[i].y : contacts[i].x;
        uint16_t y = swap_xy ? contacts[i].x : contacts[i].y;
        out[4 * i] = (uint8_t)(x >> 8);
        out[4 * i + 1] = (uint8_t)x;
        out[4 * i + 2] = (uint8_t)(y >> 8);
        out[4 * i + 3] = (uint8_t)y;
        out[40 + i] = contacts[i].width;
    }
    out[50] = out[51] = 0;
    out[52] = (uint8_t)((tag << 4) | active_count);
    out[53] = (uint8_t)metadata;
    out[54] = (uint8_t)(metadata >> 8);
    finish_checksum(out);
    return HX_REPORT_BYTES;
}

int hx_report_release(uint8_t *out, size_t capacity)
{
    if (!out || capacity < HX_REPORT_BYTES)
        return -1;
    for (unsigned i = 0; i < HX_REPORT_BYTES - 1; ++i)
        out[i] = 0xff;
    finish_checksum(out);
    return HX_REPORT_BYTES;
}

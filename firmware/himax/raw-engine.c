/* SPDX-License-Identifier: GPL-2.0-only */
#include "raw-engine.h"

int hx_raw_engine_submit(const struct hx_raw_engine_io *io,
                          uint32_t parameter, uint32_t destination,
                          unsigned max_polls)
{
    unsigned poll;
    if (!io || !io->write || !io->read8 || !max_polls)
        return HX_RAW_ENGINE_INVALID;
#define WRITE(address, width, value) \
    do { \
        if (io->write(io->context, UINT32_C(address), width, value)) \
            return HX_RAW_ENGINE_IO; \
    } while (0)
    WRITE(0x800300c8, 4u, parameter);
    WRITE(0x800300d4, 4u, parameter);
    WRITE(0x80030034, 1u, UINT32_C(0xd8));
    WRITE(0x80030038, 1u, UINT32_C(0xd8));
    WRITE(0x800300c0, 1u, UINT32_C(0));
    WRITE(0x800300b8, 2u, UINT32_C(0x5be0));
    WRITE(0x800300bc, 2u, destination & UINT32_C(0xffff));
    WRITE(0x80030000, 1u, UINT32_C(1));
#undef WRITE
    for (poll = 0; poll < max_polls; ++poll) {
        uint8_t busy;
        if (io->read8(io->context, UINT32_C(0x80030000), &busy))
            return HX_RAW_ENGINE_IO;
        if (!busy)
            return HX_RAW_ENGINE_OK;
    }
    return HX_RAW_ENGINE_TIMEOUT;
}

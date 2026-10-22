/* SPDX-License-Identifier: GPL-2.0-only */
#include <stdint.h>
#include <nds32_intrinsic.h>
struct diagnostic_result {
    uint32_t signature, version, state, counter, checksum, psw, ivb;
};
volatile struct diagnostic_result result __attribute__((section(".mailbox")));
void diagnostic_main(void)
{
    result.signature = UINT32_C(0x48584447); /* HXDG */
    result.version = 1;
    result.state = 1;
    result.counter = 0;
    result.checksum = 0;
    for (uint32_t n = 1; n <= 4096; ++n) {
        result.checksum += n;
        result.counter = n;
    }
    result.psw = __nds32__mfsr(NDS32_SR_PSW);
    result.ivb = __nds32__mfsr(NDS32_SR_IVB);
    result.state = 2;
}

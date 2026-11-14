/* SPDX-License-Identifier: MIT
 * brotli-dec: stdin (a Brotli stream) to stdout, nothing else. For the A22x recovery, which
 * reads *.new.dat.br of block-based ROM packages through it (2026-11-13). Built static from
 * google/brotli 1.1.0 (c/common, c/dec). Exit 0: whole stream; 1: damaged or cut; 2: I/O. */
#include <brotli/decode.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
static uint8_t in[1 << 16], out[1 << 18];
int main(void) {
    BrotliDecoderState *s = BrotliDecoderCreateInstance(NULL, NULL, NULL);
    if (!s) return 2;
    size_t avail_in = 0; const uint8_t *next_in = in; int eof = 0;
    for (;;) {
        if (!avail_in && !eof) {
            ssize_t n = read(0, in, sizeof in);
            if (n < 0) return 2;
            if (n == 0) eof = 1;
            avail_in = (size_t)n; next_in = in;
        }
        size_t avail_out = sizeof out; uint8_t *next_out = out;
        BrotliDecoderResult r = BrotliDecoderDecompressStream(s, &avail_in, &next_in, &avail_out, &next_out, NULL);
        size_t have = sizeof out - avail_out, done = 0;
        while (done < have) { ssize_t w = write(1, out + done, have - done); if (w <= 0) return 2; done += (size_t)w; }
        /* The stream's end must be the input's end: bytes still buffered, or more to read, are
         * not part of it (2026-11-13 review: buffered trailing bytes were accepted). */
        if (r == BROTLI_DECODER_RESULT_SUCCESS) {
            if (avail_in) return 1;
            if (eof) return 0;
            ssize_t n = read(0, in, 1);
            return n < 0 ? 2 : n > 0 ? 1 : 0;
        }
        if (r == BROTLI_DECODER_RESULT_ERROR) return 1;
        if (r == BROTLI_DECODER_RESULT_NEEDS_MORE_INPUT && eof) return 1;
    }
}

# Open HX83112F report encoder

`firmware/himax/report.c` is independently written GPL-2.0-only C for the
normal ten-slot report and the all-release report. It is one component of
an unfinished open controller firmware. It does not scan electrodes, track
contacts, access registers or replace the installed stock program.

## Wire contract

The normal packet contains 56 bytes:

| Offset | Length | Meaning |
| --- | --- | --- |
| 0 | 40 | Ten pairs of big-endian unsigned16 coordinates |
| 40 | 10 | One width byte per slot |
| 50 | 2 | Zero padding |
| 52 | 1 | Four-bit tag followed by four-bit active count |
| 53 | 2 | Little-endian metadata |
| 55 | 1 | Additive checksum, so the full byte sum is zero modulo256 |

The caller supplies every slot, including unused coordinates (normally
`0xffff`). Coordinate swapping happens before serialization. The recovered
stock formatter truncates its internal32-bit coordinates to16 bits; the C
interface accepts the resulting16-bit values explicitly.

Metadata has two observed meanings. The ordinary path supplies a contact
bitmap and tag15. A second mode supplies a separate state word and a selector
clamped to15. The serializer accepts both as explicit arguments; it does not
reproduce the mode-selection MMIO or the stock path's unrelated state-copy
side effect. Do not label all metadata as a bitmap.

The release packet is **55 bytes of `0xff` followed by `0x37`**. It is a
separate encoding, not a normal zero-contact packet. The normal packet is not
padded to128 bytes: the stock wrapper can append a separate diagnostic payload
in selected modes. That extension is not implemented here.

Null arguments, insufficient output capacity, counts above10 and tags above15
are rejected before any output write. The caller owns output storage and
must not overlap it with input contacts.

## Reproducible verification

A normal host compiler suffices for the standalone contract test:

```sh
python3 tools/firmware/test-himax-report.py
```

Its optional private-firmware comparison uses the exact supported image and
a deliberately narrow instruction interpreter. It checks2000 generated
inputs across both footer modes and axis swapping. That interpreter is an
analysis helper, not an emulator of the complete controller.

A separate comparison executes both the unmodified stock formatter and the
compiled open NDS32 C encoder in the Andes CPU simulator. The129 cases cover
both footer modes, axis swapping and the release packet:

```sh
python3 tools/firmware/test-himax-report-vendor.py \
  --firmware /path/to/Himax_firmware_fw_jdi.bin \
  --toolchain /path/to/nds32le-elf-newlib-v3 \
  --receipt /tmp/himax-report-verification.json
```

The original image is SHA-256 gated. Its payload and temporary ELF files are
created only in a temporary directory and removed after testing. The public
repository contains no original firmware payload or disassembly. Test inputs
are synthetic.

The historical official [Andes v3 toolchain release](https://github.com/andestech/nds-toolchain/releases/tag/ast-v3_2_3-release-linux)
provides GCC4.9.4 and the NDS32 GDB simulator. The tested archive
`nds32le-elf-newlib-v3.txz` has SHA-256
`bd9a440e40a6041cc21302525a30b133f371c1ab97dd2dc772e048102efd79ca`.
Compilation explicitly disables EX9 and IFC and links without relaxation;
the open encoder has no undefined runtime helpers. The original program's
EX9/IFC instructions execute in the vendor simulator.

These checks establish packet equivalence for tested inputs. They do not
establish peripheral timing, real touch detection, firmware startup or safe
hardware restoration. See [signal components](HIMAX-SIGNAL-COMPONENTS.md)
and [the firmware analysis](HIMAX-OPEN-FIRMWARE.md) for remaining work.

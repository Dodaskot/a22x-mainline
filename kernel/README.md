# Kernel

The a22x kernel is Linux `v7.2` plus the commits in `patches/`.
The patches were developed with heavy use of AI coding assistants (Claude
and Codex) and tested on the real phone as described in each release report.

The full source is at https://github.com/Dodaskot/linux-a22x, branch `a22x`.
Its **143 commits above v7.2** match this repository's patch series.
Current source (kernel #107, release v144):

- Commit: `79a09eba25ef8cb4e03a98325c6ec1e606961810`.
- Tree: `ed30c94c761a0e49d6206b94751c7b7e74e73d47`.
- Tags: `a22x-kernel107` and `a22x-v144`.
- Config: `config/a22x-v144.config`, SHA-256
  `aca24ae1a7cd143127968006a6d9329e0eb7458a49016fe096bcb97327a52364`.

Historical tags `a22x-v135` (#95), `a22x-v142` (#100),
`a22x-v143` (#101), and `a22x-kernel102` retain their original meaning.
See [source identity and validation](../docs/KERNEL-SYNC-20261104.md)
and the [prebuilt v144 image](../releases/v144/README.md).

## BOOT kernel #121 and recovery kernel #120

Both kernels add five commits to kernel #107 (`79a09eba2`). The first four are
the same in both; the fifth is each kernel's own boot splash. They are published
as separate branches and as patch series in this repository:

| | BOOT kernel #121 | Recovery kernel #120 |
|---|---|---|
| Branch / tag | [`a22x-boot`](https://github.com/Dodaskot/linux-a22x/tree/a22x-boot) / `a22x-boot-kernel121` | [`a22x-recovery`](https://github.com/Dodaskot/linux-a22x/tree/a22x-recovery) / `a22x-recovery-kernel120` |
| Commit / tree | `4fa8876fa058…` / `ca59160d9166…` | `75bd4b80ecdc…` / `68a6851dce3e…` |
| Patches above #107 | [a22x-boot/](a22x-boot/) | [a22x-recovery/](a22x-recovery/) |
| Config | [a22x-boot-k121.config](config/a22x-boot-k121.config) | [a22x-recovery-k120.config](config/a22x-recovery-k120.config) |
| Release string | `7.2.0-a22x+` | `7.2.0-a22x-recovery+` |
| Used in | [v145 boot.img](../releases/v145/README.md) | [A22x recovery 0.6.124](../recovery/README.md) |

1. `usb: musb: gadget: keep OUT packets that arrive before the next request`
2. `usb: gadget: f_ncm: retry the timeout flush while u_ether is busy`
3. `usb: musb: mediatek: re-attach the device role after system sleep`
4. `drm/mediatek: change the refresh rate through the DSI front porch`
5. `drm/mediatek: draw the A22x boot splash` (BOOT: a moving light until a
   compositor or greeter takes the display) or
   `drm/mediatek: draw the A22x recovery boot splash` (a progress bar that the
   recovery interface finishes)

Applying each series to `79a09eba2` gives exactly the tree of its branch. The
BOOT payload of v145 is byte-identical to the #121 build output. The #121
config differs from `a22x-v144.config` only in the boot splash options and in
Landlock (with `SECURITY_NETWORK` and `SECURITY_PATH`). The recovery config is
a separate, smaller configuration that keeps XZ and ZSTD initramfs support.

## Building

```fish
./build.sh
# Or clone the complete published source:
git clone -b a22x https://github.com/Dodaskot/linux-a22x
# Use tag a22x-v144 and config/a22x-v144.config for the v144 source.
```

Before building, `build.sh` checks the source tree: no unfinished
`git am`/rebase/merge, no uncommitted or untracked changes, and exactly the
diffs of `patches/` in order on top of `v7.2`. Otherwise it stops; it never
resets or deletes anything. To build a tree with other changes on purpose,
run `env A22X_DEV_TREE=1 ./build.sh <tree>`; the output is labelled as a
developer tree. An existing output directory keeps its existing .config;
use a new output directory to select the new v144 default.

The v144 config adds `CONFIG_POWER_RESET_A22X_REBOOT=y` to the historical
v135 config. RF-LDO LP observations and stock LP initialization remain
opt-in kernel parameters. They do not establish a working modem; the exact
parameters carried by the published image are documented in its manifest.

## Updating

1. Commit each logical kernel change separately, without merges or empty commits.
2. Regenerate patches with
   `kernel/update-patches.sh [tree] [branch] [base]`
   (defaults `~/linux-a22x a22x v7.2`). Private `Claude-Session:` trailers
   are removed.
3. Save a changed configuration as a new `config/a22x-vNNN.config` and update
   the build default for the accepted release.
4. Verify ordered patch identities and the tree produced by applying the
   complete series to the declared base.
5. Publish the kernel branch and accepted-build tags, then this repository
   to its GitLab primary and GitHub mirror. Preserve fast-forward history.
6. For a binary release, publish the tested image unchanged with its checksum,
   source/config identity, component manifest and explicit validation limits.

Rebasing onto a newer Linux release also requires updating `BASE` and
`BASE_COMMIT` in `build.sh`, `BASE` in `update-patches.sh` and the base
references in this file.

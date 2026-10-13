# Kernel

The a22x kernel is Linux `v7.2` plus the commits in `patches/`.
The patches were developed with heavy use of AI coding assistants (Claude
and Codex) and tested on the real phone.

The historical v135 tree is published at
https://github.com/Dodaskot/linux-a22x, tag `a22x-v135` (kernel build #95).
This repository's series now also contains patches 0132–0135 tested in v142/#100.
The kernel mirror has not been updated as part of this change; use the series
here to build the current source. Its tree hash is
`e0b3aaca697f1a53d8edb402636450cd4a35a111`.

## Building

```sh
./build.sh                    # clone v7.2 from kernel.org, git am, build
# historical v135 source only:
git clone -b a22x-v135 https://github.com/Dodaskot/linux-a22x
```

Before building, `build.sh` checks the source tree: no unfinished
`git am`/rebase/merge, no uncommitted or untracked changes, and exactly the
diffs of `patches/` in order on top of `v7.2`. Otherwise it stops; it never
resets or deletes anything. To build a tree with other changes on purpose,
run `A22X_DEV_TREE=1 ./build.sh <tree>`; the output is then labelled as a
developer tree, not the published baseline.

## Updating

1. Commit the change on branch `a22x` of the kernel tree (one logical change
   per commit, no merges, no empty commits).
2. Regenerate the patches: `kernel/update-patches.sh [tree] [branch] [base]`
   (defaults `~/linux-a22x a22x v7.2`). Commits are exported as they are;
   `Claude-Session:` trailers are removed.
3. If `.config` changed, copy it to `config/` (for a new accepted build,
   as a new `a22x-vNNN.config`).
4. Commit this repository, then push both:
   - kernel: `git push origin a22x` (and a tag `a22x-vNNN` for accepted builds)
   - this repository: GitLab `origin` and GitHub `github`.

Rebasing onto a newer Linux release changes the base: update `BASE` and
`BASE_COMMIT` in `build.sh`, `BASE` in `update-patches.sh`, and the `v7.2`
references in this file.

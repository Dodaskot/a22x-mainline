# Kernel

The a22x kernel is Linux `v7.2` plus the commits in `patches/`.
The patches were developed with heavy use of AI coding assistants (Claude
and Codex) and tested on the real phone.

The complete source is published at
https://github.com/Dodaskot/linux-a22x, branch `a22x`. Its 137 commits above
`v7.2` match this repository's patch series. Current tree:
`043297180017cae94b2574a598b9075d5cb064dd` (kernel #102).

The mirror also retains tags `a22x-v135` (#95), `a22x-v142` (#100),
`a22x-v143` (#101), and `a22x-kernel102`. The latest prebuilt release remains
v143; patch 0137 adds the #102 Himax teardown change to the source only.
See [publication, source identity and validation](../docs/KERNEL-SYNC-20261103.md).

## Building

```sh
./build.sh                    # clone v7.2 from kernel.org, git am, build
# full current source, equivalent to the patch series:
git clone -b a22x https://github.com/Dodaskot/linux-a22x
# use tag a22x-v143 when building modules for the published v143 image
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

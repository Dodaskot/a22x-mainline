# Kernel

The a22x kernel is Linux `v7.2` plus the commits in `patches/`.
The patches were developed with heavy use of AI coding assistants (Claude
and Codex) and tested on the real phone.

The same commits are published as a git tree:
https://github.com/Dodaskot/linux-a22x (fork of `torvalds/linux`)

- branch `a22x` — current development line, always `v7.2` + the patches here
- tag `a22x-v135` — the accepted v135 baseline (kernel build #95)

`patches/` and the tree must describe the same commits. The tree hash after
`git am patches/*.patch` on `v7.2` equals `git rev-parse a22x^{tree}`.

## Building

```sh
./build.sh                    # clone v7.2 from kernel.org, git am, build
# or work directly in the git tree:
git clone -b a22x https://github.com/Dodaskot/linux-a22x
```

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

Rebasing onto a newer Linux release changes the base: update `BASE`, the
`v7.2` references in `build.sh` and in this file.

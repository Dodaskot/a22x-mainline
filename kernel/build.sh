#!/bin/bash
# Fetch Linux v7.2, apply the a22x series and build Image.gz + DTBs.
# usage: ./build.sh [linux-dir] [out-dir]     (LLVM/Clang toolchain in PATH)
#
# An existing linux-dir must be exactly v7.2 + patches/ (same diffs, in
# order, clean worktree, no unfinished git am/rebase), otherwise the build
# stops. A22X_DEV_TREE=1 builds such a tree anyway, labelled as a developer
# tree rather than the published baseline.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
SRC=${1:-$here/linux}
OUT=${2:-$here/out}
BASE=v7.2
BASE_COMMIT=8d3ae59288f1e7d58d76558a6ee96d533bc5019f	# commit of tag $BASE

die() {
	echo "build.sh: $*" >&2
	exit 1
}

# Prints what differs from $BASE + patches/; fails if anything does.
check_tree() {
	local g=(git -C "$SRC") op n want bad=0

	for op in rebase-apply rebase-merge MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
		if [ -e "$("${g[@]}" rev-parse --path-format=absolute --git-path $op)" ]; then
			echo "unfinished git operation in $SRC ($op)"
			bad=1
		fi
	done
	if [ -n "$("${g[@]}" status --porcelain)" ]; then
		echo "uncommitted or untracked changes in $SRC"
		bad=1
	fi
	if ! (cd "$here/patches" && ls *.patch) | cmp -s - "$here/patches/series"; then
		echo "patches/series does not list exactly the patches/*.patch files"
		bad=1
	fi
	if ! "${g[@]}" merge-base --is-ancestor "$BASE_COMMIT" HEAD 2>/dev/null; then
		echo "HEAD is not based on $BASE ($BASE_COMMIT)"
		return 1
	fi
	n=$("${g[@]}" rev-list --count "$BASE_COMMIT..HEAD")
	want=$(ls "$here"/patches/*.patch | wc -l)
	if [ "$n" != "$want" ]; then
		echo "$n commits on top of $BASE, patches/ has $want"
		return 1
	fi
	# same diff per commit, in order (patch-id ignores messages and hashes)
	if ! cmp -s <(cat "$here"/patches/*.patch | git patch-id --stable | cut -d' ' -f1) \
		    <("${g[@]}" log --reverse -p --format='commit %H' "$BASE_COMMIT..HEAD" |
		     git patch-id --stable | cut -d' ' -f1); then
		echo "commits on top of $BASE differ from patches/"
		bad=1
	fi
	return $bad
}

if [ ! -e "$SRC/.git" ]; then
	git clone --depth 1 --branch "$BASE" \
		https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git "$SRC"
	git -C "$SRC" checkout -b a22x
	git -C "$SRC" am "$here"/patches/*.patch ||
		die "git am failed in $SRC; resolve it there, or remove $SRC and run again"
fi

if problems=$(check_tree); then
	label="baseline: $BASE + $(wc -l < "$here/patches/series") patches from kernel/patches (tree $(git -C "$SRC" rev-parse 'HEAD^{tree}'))"
elif [ "${A22X_DEV_TREE:-0}" = 1 ]; then
	echo "$problems" >&2
	label="DEVELOPER TREE, not the published baseline: $(git -C "$SRC" describe --always --dirty)"
	echo "build.sh: $label" >&2
else
	echo "$problems" >&2
	die "$SRC is not $BASE + kernel/patches. Fix it (finish or 'git am --abort', commit or stash changes, regenerate patches with update-patches.sh), or build it deliberately as a developer tree: A22X_DEV_TREE=1 $0 $*"
fi

mkdir -p "$OUT"
if [ -f "$OUT/.config" ]; then
	config="existing $OUT/.config"
else
	cp "$here/config/a22x-v135.config" "$OUT/.config"
	config="config/a22x-v135.config"
fi
MAKE="make -C $SRC O=$OUT ARCH=arm64 LLVM=1"
$MAKE olddefconfig
$MAKE -j"$(nproc)" Image.gz modules \
	mediatek/mt6833-samsung-a22x.dtb \
	mediatek/mt6833-samsung-a22x-native-display.dtb \
	mediatek/mt6833-samsung-a22x-nfc-experimental.dtb
ls -l "$OUT/arch/arm64/boot/Image.gz" "$OUT"/arch/arm64/boot/dts/mediatek/mt6833-samsung-a22x*.dtb
echo "source: $label"
echo "config: $config"

#!/bin/bash
# Regenerate kernel/patches/ from a kernel tree: one patch per commit in
# <base>..<branch>. Run after committing kernel changes, then commit this
# repository together with the pushed kernel branch/tag.
# usage: kernel/update-patches.sh [kernel-tree] [branch] [base]
#        defaults: ~/linux-a22x a22x v7.2
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
TREE=${1:-$HOME/linux-a22x}
BRANCH=${2:-a22x}
BASE=${3:-v7.2}

git -C "$TREE" rev-parse -q --verify "$BASE^{commit}" >/dev/null
git -C "$TREE" rev-parse -q --verify "$BRANCH^{commit}" >/dev/null
if [ -n "$(git -C "$TREE" rev-list --min-parents=2 "$BASE..$BRANCH")" ]; then
	echo "merge commits in $BASE..$BRANCH; rebase onto $BASE first" >&2
	exit 1
fi
nonempty=$(git -C "$TREE" log --format=%h "$BASE..$BRANCH" -- . | wc -l)
total=$(git -C "$TREE" rev-list --count "$BASE..$BRANCH")
if [ "$nonempty" != "$total" ]; then
	echo "$((total - nonempty)) empty commit(s) in $BASE..$BRANCH; drop them first" >&2
	exit 1
fi

rm -f "$here"/patches/*.patch "$here"/patches/series
git -C "$TREE" format-patch -q --zero-commit --no-signature \
	-o "$here/patches" "$BASE..$BRANCH"
# Private session links never belong in published patches.
sed -i '/^Claude-Session: /d' "$here"/patches/*.patch
(cd "$here/patches" && ls *.patch > series)

echo "$total patches from $BASE..$BRANCH ($(git -C "$TREE" rev-parse --short "$BRANCH"))"
echo "tree: $(git -C "$TREE" rev-parse "$BRANCH^{tree}")"

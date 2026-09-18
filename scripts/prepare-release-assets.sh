#!/usr/bin/env bash
# Merge the per-platform build artifacts into the file set published on the
# GitHub Release, then verify every archive against the checksum its own build
# job computed.
#
# Shared by .github/workflows/release.yml and
# .github/workflows/artifact-smoke.yml, so the smoke test exercises the real
# merge rather than a reimplementation of it.
#
# Reads  release-dist/<artifact-name>/... as laid down by download-artifact
# Writes release/ containing every archive plus a merged checksums.txt
#        (release.yml signs that checksums.txt with cosign)
set -euo pipefail

src="${1:-release-dist}"
dst="${2:-release}"

mkdir -p "$dst"
find "$src" -type f \( -name "*.tar.gz" -o -name "*.zip" \) -exec cp {} "$dst"/ \;
find "$src" -type f -name "checksums-*.txt" -exec cat {} \; > "$dst/checksums.txt"
test -s "$dst/checksums.txt"

# Refuse to publish a checksums.txt that does not describe the bytes shipped
# beside it. cosign signs this file, so an archive truncated or mis-copied
# above would otherwise ship under a valid signature and then fail closed on
# every client that verifies it (SEC-SPEC §5) -- a dead release, diagnosed
# only after publication. Fail here, before anything is signed or uploaded.
(cd "$dst" && sha256sum -c checksums.txt)

#!/bin/sh
# Build, then copy dist/ to Arch, where hk-serve serves it at / (the bundle stays at /data/).
set -eu
cd "$(dirname "$0")"
bun run build
rsync -a --delete dist/ arch:/home/lexi/calc-nvme/hackathon-20261004/web-dist/
echo "deployed: https://arch.tail87c1d7.ts.net/"

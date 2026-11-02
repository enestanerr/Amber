#!/usr/bin/env sh
# Record docs/assets/demo.gif with vhs (https://github.com/charmbracelet/vhs).
# Needs: vhs, ffmpeg, a logged-in `claude` CLI, and `uv sync --all-extras` done.
set -eu
cd "$(dirname "$0")/../examples/weekly-report"
make clean >/dev/null 2>&1 || true
mkdir -p ../../docs/assets
vhs ../../scripts/demo.tape
ls -la ../../docs/assets/demo.gif
# An MP4 for social posts:
ffmpeg -y -i ../../docs/assets/demo.gif -movflags faststart -pix_fmt yuv420p -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" ../../docs/assets/demo.mp4

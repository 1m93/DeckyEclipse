#!/usr/bin/env bash
# Build the native helpers of "Sunshine for Gaming Mode" for x86_64 SteamOS:
#   out/native/eclipse-sunshine         launcher (gets cap_sys_admin on the device)
#   out/native/libeclipse-sunshine.so   preload library (plane fix, restart redirect)
#
# Cross-compiled with zig, installed from PyPI into a cached virtualenv, so no
# Linux toolchain is needed. Targets glibc 2.17, older than any SteamOS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/out/native}"
TOOLS="${ECLIPSE_CACHE:-$HOME/.cache/decky-eclipse}/toolchain"
TARGET="x86_64-linux-gnu.2.17"

if ! "$TOOLS/bin/python" -c "import ziglang" 2>/dev/null; then
  echo "Installing zig into $TOOLS (first run only)"
  python3 -m venv "$TOOLS"
  "$TOOLS/bin/pip" -q install ziglang
fi
zig() { "$TOOLS/bin/python" -m ziglang "$@"; }

mkdir -p "$OUT"
FLAGS=(-target "$TARGET" -O2 -Wall -Wextra -Werror)
# The launcher runs with capabilities: build it hardened.
zig cc "${FLAGS[@]}" -fPIE -pie -fstack-protector-strong -D_FORTIFY_SOURCE=2 -o "$OUT/eclipse-sunshine" "$ROOT/native/sunshine_launcher.c"
zig cc "${FLAGS[@]}" -shared -fPIC -o "$OUT/libeclipse-sunshine.so" "$ROOT/native/sunshine_preload.c" -ldl
echo "Built $OUT/eclipse-sunshine and $OUT/libeclipse-sunshine.so"

#!/usr/bin/env bash
# Build the frontend and produce a Decky-installable zip in ./out.
#
# The zip contains a single top-level folder named after the plugin, which is
# the layout Decky Loader expects for "Install from ZIP" in developer mode.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$ROOT/plugin.json")"
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$ROOT/package.json")"
STAGE="$ROOT/out/$NAME"

cd "$ROOT"
pnpm install --frozen-lockfile
pnpm build

rm -rf "$ROOT/out"
mkdir -p "$STAGE/dist"
cp dist/index.js "$STAGE/dist/"
cp -R bin py_modules "$STAGE/"
cp main.py plugin.json package.json LICENSE README.md "$STAGE/"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
chmod +x "$STAGE/bin/eclipse_launcher.py" "$STAGE/bin/eclipse_sunshine_root.py"
# Helpers of Sunshine for Gaming Mode (see py_modules/eclipse/sunshine_native.py).
bash "$ROOT/scripts/build-native.sh" "$STAGE/bin/native"

(cd "$ROOT/out" && zip -qr "$NAME-$VERSION.zip" "$NAME")
echo "Created out/$NAME-$VERSION.zip"

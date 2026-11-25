#!/usr/bin/env bash
# Assemble the static demo site into _site/ (used by the Pages workflow and for local preview).
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf _site
mkdir -p _site/py/vast_hls_mcp _site/examples
cp site/* _site/
# Ship every module except the MCP server (it needs the mcp SDK, which the browser demo does not use).
files=()
for f in src/vast_hls_mcp/*.py; do
  name=$(basename "$f")
  [ "$name" = "server.py" ] && continue
  cp "$f" _site/py/vast_hls_mcp/
  files+=("\"$name\"")
done
printf '[%s]\n' "$(IFS=,; echo "${files[*]}")" > _site/py/manifest.json
cp tests/fixtures/* _site/examples/
touch _site/.nojekyll
echo "built _site/ ($(find _site -type f | wc -l | tr -d ' ') files)"

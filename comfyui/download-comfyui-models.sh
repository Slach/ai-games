#!/usr/bin/env bash
# Download ComfyUI models from HuggingFace
# Reads model_urls.txt and downloads missing models
#
# Usage:
#   ./comfyui/download-comfyui-models.sh              # Download all models
#   ./comfyui/download-comfyui-models.sh --check       # Only check which are missing
#   ./comfyui/download-comfyui-models.sh ipadapter     # Only download IP-Adapter models

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="${COMFYUI_MODELS_DIR:-$HOME/.cache/comfyui}"
URLS_FILE="${SCRIPT_DIR}/model_urls.txt"
FILTER="${1:-}"

if [ ! -f "$URLS_FILE" ]; then
    echo "ERROR: model_urls.txt not found at $URLS_FILE"
    exit 1
fi

mkdir -p "$MODELS_DIR"

download_if_missing() {
    local url="$1"
    local rel_path="$2"
    local dest="$MODELS_DIR/$rel_path"
    local dest_dir

    dest_dir="$(dirname "$dest")"
    mkdir -p "$dest_dir"

    # Apply filter if set
    if [ -n "$FILTER" ] && ! echo "$rel_path" | grep -qi "$FILTER"; then
        return
    fi

    if [ -f "$dest" ]; then
        local size
        size=$(stat -c%s "$dest" 2>/dev/null || stat -f%z "$dest" 2>/dev/null || echo "0")
        if [ "$size" -gt 1000000 ]; then
            echo "✅ EXISTS  ${rel_path} ($(numfmt --to=iec $size 2>/dev/null || echo "${size}B"))"
            return
        fi
        echo "⚠️  SMALL   ${rel_path} ($size bytes), re-downloading..."
    fi

    echo "⬇️  DOWNLOAD ${rel_path}"
    echo "   From: $url"
    wget -q --show-progress -O "$dest" "$url" || {
        echo "   ❌ FAILED: wget exit code $?"
        rm -f "$dest"
        return 1
    }
    echo "   ✅ Done"
}

echo "=== ComfyUI Model Downloader ==="
echo "Models dir: $MODELS_DIR"
echo "URLs file:  $URLS_FILE"
if [ -n "$FILTER" ]; then
    echo "Filter:     $FILTER"
fi
echo ""

# Parse URLs file
# Format: <url> -> <relative_path>
# Use ' -> ' as separator (IFS cannot handle multi-char, so use sed)
grep -vE '^\s*(#|$)' "$URLS_FILE" | while IFS= read -r line; do
    # Split on ' -> ' (space-dash-greaterthan-space)
    url="${line%% -> *}"
    rel_path="${line##* -> }"
    url="$(echo "$url" | xargs)"
    rel_path="$(echo "$rel_path" | xargs)"
    if [ -n "$url" ] && [ -n "$rel_path" ]; then
        download_if_missing "$url" "$rel_path"
    fi
done

echo ""
echo "=== Download complete ==="

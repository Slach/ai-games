#!/bin/bash

CUR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "${CUR_DIR}"

# Check if models_urls.txt exists
if [[ ! -f "comfyui/models_urls.txt" ]]; then
    echo "Error: comfyui/models_urls.txt not found"
    exit 1
fi

uv venv --force
uv install comfy-cli
source ./.venv/bin/activate

# Read models_urls.txt line by line
while IFS= read -r line || [[ -n "$line" ]]; do
    # Skip empty lines and comments
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue

    # Extract relative_path/destination_file and url from the line
    relative_path_file=$(echo "$line" | awk '{print $1}')
    url=$(echo "$line" | awk '{print $2}')

    # Use the specified command format
    comfy model download --url "$url" --relative-path "${HOME}/.cache/comfyui/${relative_path_file}"
done < comfyui/models_urls.txt

cd -

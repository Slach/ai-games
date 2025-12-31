#!/bin/bash

CUR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "${CUR_DIR}"

# Check if models_urls.txt exists
if [[ ! -f "comfyui/models_urls.txt" ]]; then
    echo "Error: comfyui/models_urls.txt not found"
    exit 1
fi

uv venv --allow-existing
uv pip install comfy-cli
source ./.venv/bin/activate

# Read models_urls.txt line by line
while IFS= read -r line || [[ -n "$line" ]]; do
    # Skip empty lines and comments
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue

    # Extract relative_path/destination_file and url from the line
    relative_path_file=$(echo "$line" | awk '{print $1}')
    url=$(echo "$line" | awk '{print $2}')

    # Skip lines that don't have both relative_path_file and url
    if [[ -z "$relative_path_file" ]] || [[ -z "$url" ]]; then
        echo "Warning: Skipping line with invalid format: $line"
        continue
    fi

    # Extract destination file and relative path from the first field
    destination_file=$(basename "$relative_path_file")
    relative_path=$(dirname "$relative_path_file")

    # Use the specified command format
    comfy model download --url "$url" --relative-path "${HOME}/.cache/comfyui/${relative_path}" --filename="${destination_file}"
done < comfyui/models_urls.txt

cd -

CUR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "${CUR_DIR}"
uv venv --force 
uv install comfy-cli
source ./.venv/bin/activate

comfy model download --url https://huggingface.co/example/model.safetensors --relative-path ${HOME}/.cache/comfyui/checkpoints
cd -

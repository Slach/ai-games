CUR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
docker buildx inspect gpubuilder >/dev/null 2>&1 || docker buildx create --progress=plain --name gpubuilder \
    --driver-opt "image=moby/buildkit:buildx-stable-1-gpu" \
    --bootstrap

docker buildx --builder gpubuilder build \
  --progress=plain \
  -t comfyui:spark-full \
  -f "${CUR_DIR}/comfyui/Dockerfile.spark" \
  --load .

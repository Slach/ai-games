#!/bin/bash

if [[ "0" == $(docker network ls | grep -c spark-network) ]]; then
  echo "Creating spark-network"
  docker network create spark-network
fi

# Download and extract workflow files from Pixelle-MCP repository
echo "Downloading and extracting workflow files..."
mkdir -p pixelle-mcp/workflows
# Download and extract workflow files directly
curl -sL https://github.com/AIDC-AI/Pixelle-MCP/archive/refs/heads/main.zip -o /tmp/pixelle-workflows.zip
unzip -q -j -o /tmp/pixelle-workflows.zip "Pixelle-MCP-main/workflows/*.json" -d pixelle-mcp/workflows
rm -f /tmp/pixelle-workflows.zip

if [[ "0" == $(docker image ls | grep -c -E "comfyui.+spark-full") ]]; then
    echo "Building pixelle-mcp..."
    docker build -t pixelle:spark-full -f pixelle-mcp/Dockerfile.spark pixelle-mcp/
fi

if [[ "0" == $(docker image ls | grep -c -E "comfyui.+spark-full") ]]; then
    echo "Building comfyui..."
  ./build-comfyui-docker.sh
fi

echo "Running docker-compose up..."
docker compose up -d

ncp add --profile=ai-games pixelle http://127.0.0.1:9004/pixelle/mcp
ncp list --profile=ai-games
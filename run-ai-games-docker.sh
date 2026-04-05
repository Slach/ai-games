#!/bin/bash

if [[ "0" == $(docker network ls | grep -c spark-network) ]]; then
  echo "Creating spark-network"
  docker network create spark-network
fi

if [[ "0" == $(docker image ls | grep -c -E "comfyui.+spark-full") ]]; then
    echo "Building comfyui..."
  ./build-comfyui-docker.sh
fi

# Export current user's UID and GID for ComfyUI container
# This ensures generated files have correct ownership
export UID=$(id -u)
export GID=$(id -g)

echo "Running docker-compose up with UID=$UID, GID=$GID..."
docker compose up -d
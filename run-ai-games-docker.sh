#!/bin/bash

if [[ "0" == $(docker network ls | grep -c spark-network) ]]; then
  echo "Creating spark-network"
  docker network create spark-network
fi

if [[ "0" == $(docker image ls | grep -c -E "comfyui.+spark-full") ]]; then
    echo "Building comfyui..."
  ./build-comfyui-docker.sh
fi

echo "Running docker-compose up with UID=$(id -u), GID=$(id -g)..."
docker compose up -d
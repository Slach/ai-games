#!/bin/bash

if [[ "0" == $(docker network ls | grep -c spark-network) ]]; then
  echo "Creating spark-network"
  docker network create spark-network
fi

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
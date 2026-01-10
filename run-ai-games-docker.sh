#!/bin/bash

# Build pixelle-mcp
echo "Building pixelle-mcp..."
docker build -t pixelle:spark-full -f pixelle-mcp/Dockerfile.spark pixelle-mcp/

# Build comfyui
echo "Building comfyui..."
./build-comfyui-docker.sh

# Run services
echo "Running docker-compose up..."
docker-compose up -d pixelle-mcp

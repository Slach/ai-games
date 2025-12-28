#!/bin/bash

# Provisioning script to install ComfyUI plugins
# This script will be run inside the ComfyUI container during startup

echo "Starting ComfyUI plugin installation..."

# Navigate to ComfyUI custom nodes directory
cd /workspace/comfyui/custom_nodes

# Install ComfyUI-Manager first (recommended for managing other plugins)
echo "Installing ComfyUI-Manager..."
if [ ! -d "ComfyUI-Manager" ]; then
  git clone https://github.com/Comfy-Org/ComfyUI-Manager.git
else
  echo "ComfyUI-Manager already exists, skipping clone"
fi

# Install ComfyUI-TRELLIS2 for 3D generation
echo "Installing ComfyUI-TRELLIS2 for 3D generation..."
if [ ! -d "ComfyUI-TRELLIS2" ]; then
  git clone https://github.com/PozzettiAndrea/ComfyUI-TRELLIS2.git
  cd ComfyUI-TRELLIS2
  pip install -r requirements.txt
  cd ..
else
  echo "ComfyUI-TRELLIS2 already exists, skipping clone"
fi

# Install comfy-cli for workflow management
echo "Installing comfy-cli for workflow management..."
pip install comfy-cli

# Install ComfyUI-nunchaku for image and video generation
echo "Installing ComfyUI-nunchaku..."
if [ ! -d "ComfyUI-nunchaku" ]; then
  git clone https://github.com/nunchaku-tech/ComfyUI-nunchaku.git
  cd ComfyUI-nunchaku
  pip install -r requirements.txt
  cd ..
else
  echo "ComfyUI-nunchaku already exists, skipping clone"
fi

# Install ComfyUI-Lightx2vWrapper for fast video generation
echo "Installing ComfyUI-Lightx2vWrapper..."
if [ ! -d "ComfyUI-Lightx2vWrapper" ]; then
  git clone https://github.com/ModelTC/ComfyUI-Lightx2vWrapper.git
  cd ComfyUI-Lightx2vWrapper
  pip install -r requirements.txt
  cd ..
else
  echo "ComfyUI-Lightx2vWrapper already exists, skipping clone"
fi

# Install ComfyUI_Fill-ChatterBox for voice generation
echo "Installing ComfyUI_Fill-ChatterBox for voice generation..."
if [ ! -d "ComfyUI_Fill-ChatterBox" ]; then
  git clone https://github.com/filliptm/ComfyUI_Fill-ChatterBox.git
  cd ComfyUI_Fill-ChatterBox
  pip install -r requirements.txt
  cd ..
else
  echo "ComfyUI_Fill-ChatterBox already exists, skipping clone"
fi

echo "All plugins installed successfully!"
echo "Plugins installed:"
echo "- ComfyUI-Manager"
echo "- ComfyUI-TRELLIS2 (3D generation)"
echo "- comfy-cli (workflow management)"
echo "- ComfyUI-nunchaku (image/video generation)"
echo "- ComfyUI-Lightx2vWrapper (fast video generation)"
echo "- ComfyUI_Fill-ChatterBox (voice generation)"

# Return to workspace
cd /workspace
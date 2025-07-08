#!/bin/bash

# Use libtcmalloc for better memory management
TCMALLOC="$(ldconfig -p | grep -Po "libtcmalloc.so.\d" | head -n 1)"
export LD_PRELOAD="${TCMALLOC}"

# Set the desired directory
COMFYUI_DIR="/comfyui"

# Allow operators to tweak verbosity; default is DEBUG.
: "${COMFY_LOG_LEVEL:=DEBUG}"

# Check if the directory exists
if [ -d "$COMFYUI_DIR" ]; then
  # Navigate to the directory
  cd "$COMFYUI_DIR" || exit
  echo "Current directory: $(pwd)"

  # Add debug ls commands
  echo "--- DEBUG: Listing relevant model directories ---"
  # Define the base directory
  BASE_DIR="/runpod-volume/ComfyUI/models"
  echo "Listing $BASE_DIR/..."
  ls -l "$BASE_DIR/"
  # Loop through all subdirectories in the base directory
  for dir in "$BASE_DIR"/*/; do
    if [ -d "$dir" ]; then
      echo "Listing contents of ${dir}..."
      ls -l "$dir"
    fi
  done
  echo "--- END DEBUG ---"

  echo "--- DEBUG: Displaying full model directory tree ---"
  # shows the tree deep
  tree "$BASE_DIR/"
  echo "--- END DEBUG ---"

  # --- NEW: Debug ls command for /comfyui directory ---
  echo "--- DEBUG: Listing /comfyui directory before launching ComfyUI ---"
  ls -lA .
  echo "--- END DEBUG ---"

  echo "--- DEBUG: Displaying extra_model_paths.yaml contents ---"
  cat extra_model_paths.yaml
  echo "--- END DEBUG ---"

  # Start ComfyUI in the background
  echo "Starting ComfyUI server in background..."
  # Use python3 explicitly if needed, adjust flags as necessary
  python main.py --port 8188 --listen 0.0.0.0 --disable-auto-launch --extra-model-paths-config /comfyui/extra_model_paths.yaml &

  # Give ComfyUI more time to start up
  echo "Waiting 15s for ComfyUI to initialize..."
  sleep 15 

  # Start the RunPod handler in the foreground
  echo "Starting RunPod handler..."
  python /rp_handler.py
else
  # Print an error message if the directory doesn't exist
  echo "Error: Directory $COMFYUI_DIR not found."
  exit 1
fi
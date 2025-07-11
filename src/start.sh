#!/bin/bash
# Set the desired directory
COMFYUI_DIR="/comfyui"

# Check if the directory exists
if [ -d "$COMFYUI_DIR" ]; then
  # Navigate to the directory
  cd "$COMFYUI_DIR" || exit
  echo "Current directory: $(pwd)"

  # Add debug ls commands
  echo "--- DEBUG: Listing relevant model directories ---"
  echo "Listing /runpod-volume/models/..."
  ls -l /runpod-volume/models/
  echo "Listing /runpod-volume/models/vae/..."
  ls -l /runpod-volume/models/vae/
  echo "Listing /runpod-volume/models/clip/..."
  ls -l /runpod-volume/models/clip/
  echo "Listing /runpod-volume/models/diffusion_models/..."
  ls -l /runpod-volume/models/diffusion_models/
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
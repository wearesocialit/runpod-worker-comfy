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
  echo "Listing /runpod-volume/ComfyUI/models/..."
  ls -l /runpod-volume/ComfyUI/models/
  echo "Listing /runpod-volume/ComfyUI/models/vae/..."
  ls -l /runpod-volume/ComfyUI/models/vae/
  echo "Listing /runpod-volume/ComfyUI/models/clip/..."
  ls -l /runpod-volume/ComfyUI/models/clip/
  echo "Listing /runpod-volume/ComfyUI/models/diffusion_models/..."
  ls -l /runpod-volume/ComfyUI/models/diffusion_models/
  echo "--- END DEBUG ---"


  # Run the Python script with arguments (This was the state that passed validation)
  echo "Starting ComfyUI directly (reverted state)..."
  python main.py --dont-print-server --port 8188 --listen 0.0.0.0 "$@"
else
  # Print an error message if the directory doesn't exist
  echo "Error: Directory $COMFYUI_DIR not found."
  exit 1
fi
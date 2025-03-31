#!/bin/bash
# Set the desired directory
COMFYUI_DIR="/comfyui"

# Check if the directory exists
if [ -d "$COMFYUI_DIR" ]; then
  # Navigate to the directory
  cd "$COMFYUI_DIR" || exit
  echo "Current directory: $(pwd)"

  # Start ComfyUI in the background
  echo "Starting ComfyUI server in background..."
  # Use python3 explicitly if needed, adjust flags as necessary
  python main.py --port 8188 --listen 0.0.0.0 --disable-auto-launch &

  # Give ComfyUI a moment to start up (adjust sleep time if needed)
  sleep 5 

  # Start the RunPod handler in the foreground
  # Assuming rp_handler.py is in the src directory, adjust path if necessary
  # Use python3 explicitly if needed
  echo "Starting RunPod handler..."
  python -u /rp_handler.py 

else
  # Print an error message if the directory doesn't exist
  echo "Error: Directory $COMFYUI_DIR not found."
  exit 1
fi
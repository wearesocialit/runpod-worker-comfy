# Stage 1: Base image with common dependencies
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04 AS base

# Prevents prompts from packages asking for user input during installation
ENV DEBIAN_FRONTEND=noninteractive
# Prefer binary wheels over source distributions for faster pip installations
ENV PIP_PREFER_BINARY=1
# Ensures output from python is printed immediately to the terminal without buffering
ENV PYTHONUNBUFFERED=1 
# Speed up some cmake builds
ENV CMAKE_BUILD_PARALLEL_LEVEL=8

# Install Python, git and other base tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 \
    python3.10-dev \
    python3.10-venv \
    git \
    wget \
    libgl1 \
    libglib2.0-0 \
    # Link python3.10 to python immediately
    && ln -sf /usr/bin/python3.10 /usr/bin/python \
    # Clean lists for this layer
    && rm -rf /var/lib/apt/lists/*

# Install pip separately now that python points to 3.10, then set pip links
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    # Link pip now that it's installed
    && ln -sf /usr/bin/pip3 /usr/bin/pip \
    # Clean lists for this layer
    && rm -rf /var/lib/apt/lists/*

# Create and set permissions for ControlNet Aux caching
RUN mkdir -p /tmp/ckpts && chmod -R 777 /tmp/ckpts

# Clean up to reduce image size
RUN apt-get autoremove -y && apt-get clean -y && rm -rf /var/lib/apt/lists/*

# Install comfy-cli
# Create virtual environment for ComfyUI
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:/usr/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir comfy-cli

# Install ComfyUI
RUN /usr/bin/yes | comfy --workspace /comfyui install --cuda-version 11.8 --nvidia --version 0.3.26

# Change working directory to ComfyUI
WORKDIR /comfyui

# Install runpod
RUN pip install runpod requests

# Install other required python packages that were previously in the large install list
RUN pip install accelerate==1.7.0 numba scikit-image simpleeval "huggingface_hub==0.22.2" onnxruntime-gpu yacs websocket-client opencv-python opencv-python-headless

# Copy the custom model paths configuration BEFORE ComfyUI potentially reads defaults
# Also, rename the example file first to avoid potential conflicts
RUN mv extra_model_paths.yaml.example extra_model_paths.yaml.example.bak || true
COPY src/extra_model_paths.yaml .

# Support for the network volume
# ADD src/extra_model_paths.yaml ./

# Go back to the root
WORKDIR /

# Add scripts
ADD src/start.sh src/restore_snapshot.sh src/rp_handler.py test_input.json ./
RUN chmod +x /start.sh /restore_snapshot.sh

# Optionally copy the snapshot file
ADD *snapshot*.json /

# Restore the snapshot to install custom nodes
RUN /restore_snapshot.sh

# Start container
CMD ["/start.sh"]

# Stage 2: Download models
FROM base AS downloader

ARG HUGGINGFACE_ACCESS_TOKEN
ARG MODEL_TYPE=full

# Change working directory to ComfyUI
WORKDIR /comfyui

# Create necessary directories for models that will be copied to the final stage
# No models will be downloaded here; they are expected to be on the network volume.
RUN mkdir -p models/checkpoints models/vae models/unet models/clip

# Download checkpoints/vae/unet/clip models to include in image based on model type
RUN \
    # --- SDXL ---
    if [ "$MODEL_TYPE" = "sdxl" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading SDXL models..." && \
        wget -q -O models/checkpoints/sd_xl_base_1.0.safetensors https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors && \
        wget -q -O models/vae/sdxl_vae.safetensors https://huggingface.co/stabilityai/sdxl-vae/resolve/main/sdxl_vae.safetensors; \
    fi && \
    # --- SD3 ---
    if [ "$MODEL_TYPE" = "sd3" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading SD3 models..." && \
        wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/checkpoints/sd3_medium_incl_clips_t5xxlfp8.safetensors https://huggingface.co/stabilityai/stable-diffusion-3-medium/resolve/main/sd3_medium_incl_clips_t5xxlfp8.safetensors; \
    fi && \
    # --- FLUX.1-schnell ---
    if [ "$MODEL_TYPE" = "flux1-schnell" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading FLUX.1-schnell models..." && \
        wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/unet/flux1-schnell.safetensors https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors && \
        wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/vae/flux1-schnell-ae.safetensors https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors; \
    fi && \
    # --- FLUX.1-dev ---
    if [ "$MODEL_TYPE" = "flux1-dev" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading FLUX.1-dev models..." && \
        wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/unet/flux1-dev.safetensors https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors && \
        wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/vae/flux1-dev-ae.safetensors https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/ae.safetensors; \
    fi && \
    # --- FLUX.1-dev-fp8 ---
    if [ "$MODEL_TYPE" = "flux1-dev-fp8" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading FLUX.1-dev-fp8 models..." && \
        wget -q -O models/checkpoints/flux1-dev-fp8.safetensors https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors; \
    fi && \
    # --- Text Encoders common ---
    if [ "$MODEL_TYPE" = "flux1-schnell" ] || [ "$MODEL_TYPE" = "flux1-dev" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading common text encoders..." && \
        wget -q -O models/clip/clip_l.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors && \
        wget -q -O models/clip/t5xxl_fp8_e4m3fn.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors; \
    fi

# Ensure there's no empty continuation line before the next stage
# Stage 3: Final image
FROM base AS final

# Reverted: Copy the original config file from the base stage
COPY --from=base /comfyui/extra_model_paths.yaml /comfyui/

# Debug: List contents of /comfyui to verify copy
RUN echo "--- Listing /comfyui contents during build (final stage) ---" && ls -lA /comfyui

# Copy models from stage 2 to the final image
COPY --from=downloader /comfyui/models /comfyui/models

# Start container
CMD ["/start.sh"]

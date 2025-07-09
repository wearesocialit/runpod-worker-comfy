# Stage 1: Base image with common dependencies
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04 AS base

# Prevents prompts from packages asking for user input during installation
ENV DEBIAN_FRONTEND=noninteractive
# Prefer binary wheels over source distributions for faster pip installations
ENV PIP_PREFER_BINARY=1
# Ensures output from python is printed immediately to the terminal without buffering
ENV PYTHONUNBUFFERED=1 
# Speed up some cmake builds
ENV CMAKE_BUILD_PARALLEL_LEVEL=8

# Install system dependencies
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
    # Install pip using the system's package manager
    && apt-get install -y --no-install-recommends python3-pip \
    # Link pip now that it's installed
    && ln -sf /usr/bin/pip3 /usr/bin/pip \
    # Clean lists for this layer
    && rm -rf /var/lib/apt/lists/*

# Create and set permissions for ControlNet Aux caching
RUN mkdir -p /tmp/ckpts && chmod -R 777 /tmp/ckpts

# Create and activate the virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install all Python packages in a single layer for efficiency
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        comfy-cli \
        runpod requests \
        accelerate==1.7.0 \
        numba \
        scikit-image \
        simpleeval \
        onnxruntime-gpu \
        yacs \
        websocket-client \
        opencv-python-headless \
        transformers \
        diffusers \
        "huggingface_hub==0.22.2"

# Install ComfyUI
RUN /usr/bin/yes | comfy --workspace /comfyui install --cuda-version 12.6 --nvidia --version 0.3.26

# Set working directory for app-specific commands
WORKDIR /comfyui

# Copy the custom model paths configuration
# Also, rename the example file first to avoid potential conflicts
RUN mv extra_model_paths.yaml.example extra_model_paths.yaml.example.bak || true
COPY src/extra_model_paths.yaml .

# Return to root for script handling
WORKDIR /

# Add and set permissions for application scripts
ADD src/start.sh src/restore_snapshot.sh src/rp_handler.py test_input.json ./
RUN chmod +x /start.sh /restore_snapshot.sh

# Optionally copy the snapshot file and restore custom nodes
ADD *snapshot*.json /
RUN /restore_snapshot.sh

# Start container
CMD ["/start.sh"]

# =========================================================================
# Stage 2: Download models
# =========================================================================
FROM base AS downloader

ARG HUGGINGFACE_ACCESS_TOKEN
# Set default model type to 'full' to ensure models are downloaded
ARG MODEL_TYPE=full

WORKDIR /comfyui
RUN mkdir -p models/checkpoints models/vae models/unet models/clip

# Download all models in a single RUN instruction to optimize layer usage
RUN \
    if [ "$MODEL_TYPE" = "sdxl" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading SDXL models..." && \
        wget -q -O models/checkpoints/sd_xl_base_1.0.safetensors https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors && \
        wget -q -O models/vae/sdxl_vae.safetensors https://huggingface.co/stabilityai/sdxl-vae/resolve/main/sdxl_vae.safetensors; \
    fi && \
    if [ "$MODEL_TYPE" = "sd3" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading SD3 models..." && \
        wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/checkpoints/sd3_medium_incl_clips_t5xxlfp8.safetensors https://huggingface.co/stabilityai/stable-diffusion-3-medium/resolve/main/sd3_medium_incl_clips_t5xxlfp8.safetensors; \
    fi && \
    if [ "$MODEL_TYPE" = "flux1-schnell" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading FLUX.1-schnell models..." && \
        wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/unet/flux1-schnell.safetensors https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors && \
        wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/vae/flux1-schnell-ae.safetensors https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors; \
    fi && \
    if [ "$MODEL_TYPE" = "flux1-dev" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading FLUX.1-dev models..." && \
        wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/unet/flux1-dev.safetensors https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors && \
        wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/vae/flux1-dev-ae.safetensors https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/ae.safetensors; \
    fi && \
    if [ "$MODEL_TYPE" = "flux1-dev-fp8" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading FLUX.1-dev-fp8 models..." && \
        wget -q -O models/checkpoints/flux1-dev-fp8.safetensors https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors; \
    fi && \
    if [ "$MODEL_TYPE" = "flux1-schnell" ] || [ "$MODEL_TYPE" = "flux1-dev" ] || [ "$MODEL_TYPE" = "full" ]; then \
        echo "--> Downloading common text encoders..." && \
        wget -q -O models/clip/clip_l.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors && \
        wget -q -O models/clip/t5xxl_fp8_e4m3fn.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors; \
    fi

# =========================================================================
# Stage 3: Final image
# =========================================================================
FROM base AS final

# Copy application config files from the base stage
COPY --from=base /comfyui/extra_model_paths.yaml /comfyui/

# Copy downloaded models from the downloader stage
COPY --from=downloader /comfyui/models /comfyui/models

# Start container
CMD ["/start.sh"]

import runpod
from runpod.serverless.utils import rp_upload, rp_cleanup
import json
import urllib.request
import urllib.parse
import time
import os
import requests
import base64
from io import BytesIO
import uuid # Added for unique filenames

# Time to wait between API check attempts in milliseconds
COMFY_API_AVAILABLE_INTERVAL_MS = 50
# Maximum number of API check attempts
COMFY_API_AVAILABLE_MAX_RETRIES = 500
# Maximum time to wait for ComfyUI /object_info to be ready (in seconds)
COMFY_API_READY_TIMEOUT = int(os.environ.get("COMFY_API_READY_TIMEOUT", 60)) # Increased default to 60s
# Time to wait between poll attempts in milliseconds
COMFY_POLLING_INTERVAL_MS = int(os.environ.get("COMFY_POLLING_INTERVAL_MS", 250))
# Maximum number of poll attempts
COMFY_POLLING_MAX_RETRIES = int(os.environ.get("COMFY_POLLING_MAX_RETRIES", 500))
# Host where ComfyUI is running
COMFY_HOST = "127.0.0.1:8188"
# Enforce a clean state after each job is done
# see https://docs.runpod.io/docs/handler-additional-controls#refresh-worker
REFRESH_WORKER = os.environ.get("REFRESH_WORKER", "false").lower() == "true"


def validate_input(job_input):
    """
    Validates the input for the handler function

    Args:
        job_input (dict): The input data to validate.

    Returns:
        tuple: A tuple containing the validated data and an error message, if any.
               The structure is (validated_data, error_message).
    """
    # Validate if job_input is provided
    if job_input is None:
        return None, "Please provide input"

    # Check if input is a string and try to parse it as JSON
    if isinstance(job_input, str):
        try:
            job_input = json.loads(job_input)
        except json.JSONDecodeError:
            return None, "Invalid JSON format in input"

    # Validate 'workflow' in input
    workflow = job_input.get("workflow")
    if workflow is None:
        return None, "Missing 'workflow' parameter"

    # Validate 'images' in input, if provided
    images = job_input.get("images")
    if images is not None:
        if not isinstance(images, list) or not all(
            "name" in image and "image" in image for image in images
        ):
            return (
                None,
                "'images' must be a list of objects with 'name' and 'image' keys",
            )

    # Return validated data and no error
    return {"workflow": workflow, "images": images}, None


def wait_for_comfy_api_ready(url, timeout_seconds=COMFY_API_READY_TIMEOUT):
    """
    Waits until the ComfyUI API /object_info endpoint is responsive and 
    indicates core nodes (like VAELoader) are available.

    Args:
        url (str): The base URL of the ComfyUI server (e.g., http://127.0.0.1:8188).
        timeout_seconds (int): Maximum time to wait in seconds.

    Returns:
        bool: True if the API becomes ready within the timeout, False otherwise.
    """
    start_time = time.time()
    object_info_url = f"{url}/object_info"
    print(f"runpod-worker-comfy - Waiting for ComfyUI API at {object_info_url} to be ready...")
    while True:
        if time.time() - start_time > timeout_seconds:
            print(f"runpod-worker-comfy - Timeout waiting for ComfyUI API to be ready after {timeout_seconds}s.")
            return False
        try:
            response = requests.get(object_info_url, timeout=5) # Add timeout to request
            if response.status_code == 200:
                try:
                    object_info = response.json()
                    # Check if a common core node exists in the response
                    # Adjust node name if necessary for your specific setup
                    if isinstance(object_info, dict) and "VAELoader" in object_info:
                        print(f"runpod-worker-comfy - ComfyUI API is ready.")
                        return True
                    else:
                        print(f"runpod-worker-comfy - API up, but /object_info doesn't contain expected nodes yet. Retrying...")
                except json.JSONDecodeError:
                     print(f"runpod-worker-comfy - API up, but /object_info returned invalid JSON. Retrying...")
            # Optionally handle other status codes if needed
        except requests.RequestException as e:
            # print(f"runpod-worker-comfy - API not reachable yet ({e}). Retrying...") # Verbose logging
            pass # Ignore connection errors and keep trying
        
        time.sleep(COMFY_API_AVAILABLE_INTERVAL_MS / 1000) # Use interval for sleep


def upload_images(images):
    """
    Upload a list of base64 encoded images to the ComfyUI server using the /upload/image endpoint.

    Args:
        images (list): A list of dictionaries, each containing the 'name' of the image and the 'image' as a base64 encoded string.
        server_address (str): The address of the ComfyUI server.

    Returns:
        list: A list of responses from the server for each image upload.
    """
    if not images:
        return {"status": "success", "message": "No images to upload", "details": []}

    responses = []
    upload_errors = []

    print(f"runpod-worker-comfy - image(s) upload")

    for image in images:
        name = image["name"]
        image_data = image["image"]
        blob = base64.b64decode(image_data)

        # Prepare the form data
        files = {
            "image": (name, BytesIO(blob), "image/png"),
            "overwrite": (None, "true"),
        }

        # POST request to upload the image
        response = requests.post(f"http://{COMFY_HOST}/upload/image", files=files)
        if response.status_code != 200:
            upload_errors.append(f"Error uploading {name}: {response.text}")
        else:
            responses.append(f"Successfully uploaded {name}")

    if upload_errors:
        print(f"runpod-worker-comfy - image(s) upload with errors")
        return {
            "status": "error",
            "message": "Some images failed to upload",
            "details": upload_errors,
        }

    print(f"runpod-worker-comfy - image(s) upload complete")
    return {
        "status": "success",
        "message": "All images uploaded successfully",
        "details": responses,
    }


def queue_workflow(workflow):
    """
    Queue a workflow to be processed by ComfyUI

    Args:
        workflow (dict): A dictionary containing the workflow to be processed

    Returns:
        dict: The JSON response from ComfyUI after processing the workflow
    """

    # The top level element "prompt" is required by ComfyUI
    data = json.dumps({"prompt": workflow}).encode("utf-8")

    req = urllib.request.Request(f"http://{COMFY_HOST}/prompt", data=data)
    return json.loads(urllib.request.urlopen(req).read())


def get_history(prompt_id):
    """
    Retrieve the history of a given prompt using its ID

    Args:
        prompt_id (str): The ID of the prompt whose history is to be retrieved

    Returns:
        dict: The history of the prompt, containing all the processing steps and results
    """
    with urllib.request.urlopen(f"http://{COMFY_HOST}/history/{prompt_id}") as response:
        return json.loads(response.read())


def base64_encode(img_path):
    """
    Returns base64 encoded image.

    Args:
        img_path (str): The path to the image

    Returns:
        str: The base64 encoded image
    """
    with open(img_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        return f"{encoded_string}"


def process_output_images(outputs, job_id):
    """
    This function takes the "outputs" from image generation and the job ID,
    then determines the correct way to return the image, either as a direct URL
    to an AWS S3 bucket or as a base64 encoded string, depending on the
    environment configuration.

    Args:
        outputs (dict): A dictionary containing the outputs from image generation,
                        typically includes node IDs and their respective output data.
        job_id (str): The unique identifier for the job.

    Returns:
        dict: A dictionary with the status ('success' or 'error') and the message,
              which is either the URL to the image in the AWS S3 bucket or a base64
              encoded string of the image. In case of error, the message details the issue.

    The function works as follows:
    - It first determines the output path for the images from an environment variable,
      defaulting to "/comfyui/output" if not set.
    - It then iterates through the outputs to find the filenames of the generated images.
    - After confirming the existence of the image in the output folder, it checks if the
      AWS S3 bucket is configured via the BUCKET_ENDPOINT_URL environment variable.
    - If AWS S3 is configured, it uploads the image to the bucket and returns the URL.
    - If AWS S3 is not configured, it encodes the image in base64 and returns the string.
    - If the image file does not exist in the output folder, it returns an error status
      with a message indicating the missing image file.
    """

    # The path where ComfyUI stores the generated images
    COMFY_OUTPUT_PATH = os.environ.get("COMFY_OUTPUT_PATH", "/comfyui/output")

    output_images = {}

    for node_id, node_output in outputs.items():
        if "images" in node_output:
            for image in node_output["images"]:
                output_images = os.path.join(image["subfolder"], image["filename"])

    print(f"runpod-worker-comfy - image generation is done")

    # expected image output folder
    local_image_path = f"{COMFY_OUTPUT_PATH}/{output_images}"

    print(f"runpod-worker-comfy - {local_image_path}")

    # The image is in the output folder
    if os.path.exists(local_image_path):
        # Use rp_upload utility if BUCKET_ENDPOINT_URL is set
        if os.environ.get("BUCKET_ENDPOINT_URL", None):
            print("runpod-worker-comfy - Uploading image to bucket...")
            image_url = rp_upload.upload_image(job_id, local_image_path)
            rp_cleanup.clean([local_image_path]) # Clean up after upload
            return {"status": "success", "message": image_url}
        else:
            print(
                "runpod-worker-comfy - Returning base64 encoded image as no bucket is configured"
            )
            image_base64 = base64_encode(local_image_path)
            rp_cleanup.clean([local_image_path]) # Clean up after encoding
            return {"status": "success", "message": image_base64}
    else:
        print("runpod-worker-comfy - the image does not exist in the output folder")
        return {
            "status": "error",
            "message": f"the image does not exist in the specified output folder: {local_image_path}",
        }


# --- New Function to Handle Base64 Input for LoadImage ---
def preprocess_loadimage_nodes(workflow):
    """
    Scans the workflow for LoadImage nodes. If the 'image' input is base64,
    decodes it, saves it to /comfyui/input, and updates the node input.
    """
    COMFY_INPUT_PATH = os.environ.get("COMFY_INPUT_PATH", "/comfyui/input")
    os.makedirs(COMFY_INPUT_PATH, exist_ok=True) # Ensure input directory exists

    if not isinstance(workflow, dict):
        print("runpod-worker-comfy - Warning: Workflow is not a dictionary, skipping image preprocessing.")
        return workflow # Or raise an error, depending on desired behavior

    for node_id, node_data in workflow.items():
        # Ensure node_data is a dictionary and contains 'class_type' and 'inputs'
        if not isinstance(node_data, dict):
            # print(f"runpod-worker-comfy - Warning: Node data for ID {node_id} is not a dictionary, skipping.")
            continue
        
        class_type = node_data.get('class_type')
        inputs = node_data.get('inputs')

        if class_type == "LoadImage" and inputs and 'image' in inputs:
            image_input_value = inputs['image']
            if isinstance(image_input_value, str):
                try:
                    # Attempt to decode base64. This assumes raw base64, no data URI prefix.
                    image_bytes = base64.b64decode(image_input_value)
                    
                    # Generate unique filename (assuming PNG, LoadImage might handle others)
                    filename = f"rp_input_{uuid.uuid4()}.png" 
                    filepath = os.path.join(COMFY_INPUT_PATH, filename)
                    
                    # Save the decoded image
                    with open(filepath, 'wb') as f:
                        f.write(image_bytes)
                    
                    # Update the workflow input to use the filename
                    inputs['image'] = filename
                    print(f"runpod-worker-comfy - Decoded base64 input for node {node_id}, saved as {filename}")
                    
                except (base64.binascii.Error, ValueError):
                    # If it's not valid base64, assume it's already a filename and leave it.
                    # print(f"runpod-worker-comfy - Input for LoadImage node {node_id} is not base64, assuming filename: {image_input_value[:50]}...")
                    pass 
                except Exception as e:
                    # Catch other potential errors during file saving
                    print(f"runpod-worker-comfy - Error processing image for node {node_id}: {e}")
            # else:
                # print(f"runpod-worker-comfy - Input for LoadImage node {node_id} is not a string, skipping base64 check.")

    return workflow # Return the potentially modified workflow

# --- End New Function ---


def handler(job):
    """
    Runs the handler function.

    Args:
        job (dict): The job data.
    """
    # Use the more robust readiness check
    api_ready = wait_for_comfy_api_ready(f"http://{COMFY_HOST}")
    if not api_ready:
        return {"error": "ComfyUI API did not become ready in time"}
    
    # Validate the input
    job_input = job["input"]
    validated_input, error_message = validate_input(job_input)
    if error_message:
        return {"error": error_message}

    # Extract validated data
    workflow = validated_input["workflow"]
    images = validated_input.get("images")

    # Upload images if they exist
    upload_result = upload_images(images)

    if upload_result["status"] == "error":
        return upload_result

    # --- Preprocess workflow for LoadImage base64 inputs ---
    try:
        workflow = preprocess_loadimage_nodes(workflow)
    except Exception as e:
        print(f"runpod-worker-comfy - Error during workflow preprocessing: {e}")
        return {"error": f"Error during workflow preprocessing: {e}"}
    # --- End Preprocessing ---

    # Queue the workflow
    try:
        queued_workflow = queue_workflow(workflow)
        prompt_id = queued_workflow["prompt_id"]
        print(f"runpod-worker-comfy - queued workflow with ID {prompt_id}")
    except Exception as e:
        return {"error": f"Error queuing workflow: {str(e)}"}

    # Poll for completion
    print(f"runpod-worker-comfy - wait until image generation is complete")
    retries = 0
    try:
        while retries < COMFY_POLLING_MAX_RETRIES:
            history = get_history(prompt_id)

            # Exit the loop if we have found the history
            if prompt_id in history and history[prompt_id].get("outputs"):
                break
            else:
                # Wait before trying again
                time.sleep(COMFY_POLLING_INTERVAL_MS / 1000)
                retries += 1
        else:
            return {"error": "Max retries reached while waiting for image generation"}
    except Exception as e:
        return {"error": f"Error waiting for image generation: {str(e)}"}

    # Get the generated image and return it as URL in an AWS bucket or as base64
    images_result = process_output_images(history[prompt_id].get("outputs"), job["id"])

    result = {**images_result, "refresh_worker": REFRESH_WORKER}

    return result


# Start the handler only if this script is run directly
if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})

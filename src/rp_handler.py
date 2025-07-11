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
import subprocess # Import subprocess module

# Time to wait between API check attempts in milliseconds
COMFY_API_AVAILABLE_INTERVAL_MS = 50
# Maximum number of API check attempts
COMFY_API_AVAILABLE_MAX_RETRIES = 500
# Maximum time to wait for ComfyUI /object_info to be ready (in seconds)
COMFY_API_READY_TIMEOUT = int(os.environ.get("COMFY_API_READY_TIMEOUT", 60)) # Increased default to 60s
# Time to wait between poll attempts in milliseconds
COMFY_POLLING_INTERVAL_MS = int(os.environ.get("COMFY_POLLING_INTERVAL_MS", 250))
# Maximum number of poll attempts
COMFY_POLLING_MAX_RETRIES = int(os.environ.get("COMFY_POLLING_MAX_RETRIES", 1000)) # Increased from 500 to 1000
# Host where ComfyUI is running
COMFY_HOST = "127.0.0.1:8188"
# Enforce a clean state after each job is done
# see https://docs.runpod.io/docs/handler-additional-controls#refresh-worker
REFRESH_WORKER = os.environ.get("REFRESH_WORKER", "false").lower() == "true"

# --- Debug Function to List Directories ---
def list_directory_contents(path_to_list):
    try:
        print(f"--- Listing contents of: {path_to_list} ---")
        # Use subprocess to run the ls command
        result = subprocess.run(['ls', '-lA', path_to_list], capture_output=True, text=True, check=True)
        print(result.stdout)
        print(f"--- End of listing for: {path_to_list} ---")
    except FileNotFoundError:
        print(f"Error: Directory not found: {path_to_list}")
    except subprocess.CalledProcessError as e:
        print(f"Error executing ls command for {path_to_list}: {e}")
        print(f"Stderr: {e.stderr}")
    except Exception as e:
        print(f"An unexpected error occurred while listing {path_to_list}: {e}")
# --- End Debug Function ---


def validate_input(job_input):
    """
    Validates the input for the handler function.
    Looks for workflow data under 'workflow' or 'comfy_workflow'.
    Checks 'images' format if present, but doesn't require it.

    Args:
        job_input (dict): The input data to validate.

    Returns:
        tuple: A tuple containing the validated data and an error message, if any.
               The structure is ({'extracted_workflow': workflow, 'images': images_or_none}, error_message).
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

    # Validate 'workflow' or 'comfy_workflow' in input
    workflow = job_input.get("workflow") or job_input.get("comfy_workflow") # Check both keys
    if workflow is None:
        return None, "Missing 'workflow' or 'comfy_workflow' parameter in input"
    if not isinstance(workflow, dict):
         return None, "'workflow' or 'comfy_workflow' must be a JSON object"

    # Validate 'images' in input, only if provided
    images = job_input.get("images")
    if images is not None:
        if not isinstance(images, list) or not all(
            isinstance(image, dict) and "name" in image and "image" in image for image in images
        ):
            return (
                None,
                "'images', if provided, must be a list of objects with 'name' and 'image' keys",
            )

    # Return validated data and no error
    return {"extracted_workflow": workflow, "images": images}, None # Store workflow under 'extracted_workflow'


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


def save_input_images(images):
    """
    Decodes base64 images from the input list and saves them to /comfyui/input.

    Args:
        images (list | None): A list of dictionaries, each containing 'name' 
                               and base64 'image' data, or None.

    Returns:
        dict: Status report (success or error with details).
    """
    COMFY_INPUT_PATH = os.environ.get("COMFY_INPUT_PATH", "/comfyui/input")
    os.makedirs(COMFY_INPUT_PATH, exist_ok=True)

    if not images:
        return {"status": "success", "message": "No images provided in input list."}

    save_errors = []
    saved_files = []

    print(f"runpod-worker-comfy - Processing {len(images)} image(s) from input list...")

    for image_item in images:
        try:
            name = image_item["name"]
            image_base64 = image_item["image"]
            
            # Basic check if it looks like base64
            if not isinstance(image_base64, str) or len(image_base64) < 10:
                 raise ValueError("Invalid image data format.")
                 
            image_bytes = base64.b64decode(image_base64)
            filepath = os.path.join(COMFY_INPUT_PATH, name)

            # Save the decoded image, overwriting if necessary
            with open(filepath, 'wb') as f:
                f.write(image_bytes)
            saved_files.append(name)
            print(f"runpod-worker-comfy - Saved input image to: {filepath}")

        except KeyError as e:
            error_msg = f"Missing key {e} in image item: {image_item}"
            print(f"runpod-worker-comfy - Error: {error_msg}")
            save_errors.append(error_msg)
        except (base64.binascii.Error, ValueError) as e:
            error_msg = f"Failed to decode base64 for image '{image_item.get('name', 'UNKNOWN')}': {e}"
            print(f"runpod-worker-comfy - Error: {error_msg}")
            save_errors.append(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error saving image '{image_item.get('name', 'UNKNOWN')}': {e}"
            print(f"runpod-worker-comfy - Error: {error_msg}")
            save_errors.append(error_msg)

    if save_errors:
        return {
            "status": "error",
            "message": "Errors occurred while saving input images.",
            "details": save_errors,
            "saved_files": saved_files
        }

    print(f"runpod-worker-comfy - Successfully saved input images: {saved_files}")
    return {"status": "success", "message": "Input images saved successfully.", "saved_files": saved_files}


def handler(job):
    """
    The handler function that will be called by the serverless worker.
    It validates the input, queues the workflow, polls for results,
    and returns the output.
    """
    # --- Start Directory Listing Debug ---
    print("--- Running Directory Listing Debug ---")
    list_directory_contents("/runpod-volume/models/")
    list_directory_contents("/runpod-volume/models/vae/") # Check VAE folder on volume
    list_directory_contents("/comfyui/models/") # Check models folder in container
    list_directory_contents("/comfyui/models/vae/") # Check VAE folder in container
    print("--- End Directory Listing Debug ---")


    # Wait for ComfyUI API to be ready before processing the job
    if not wait_for_comfy_api_ready(f"http://{COMFY_HOST}"):
        # If API doesn't become ready, return an error. Adjust as needed.
        return {
            "error": "ComfyUI API did not become ready within the timeout period."
        }

    job_input = job['input']

    # Validate the input using the refactored function
    validated_input, error = validate_input(job_input)
    if error:
        return {"error": error}

    # Extract validated data
    workflow = validated_input['extracted_workflow']
    images = validated_input['images']

    # Handle image uploads if images are provided
    if images:
        upload_response = upload_images(images) # Changed function name for clarity
        if upload_response['status'] == 'error':
            return {"error": upload_response['message'], "details": upload_response['details']}

    # Queue the workflow
    try:
        queued_workflow = queue_workflow(workflow)
        prompt_id = queued_workflow['prompt_id']
        print(f"runpod-worker-comfy - queued workflow with ID {prompt_id}")
        print(f"runpod-worker-comfy - wait until image generation is complete")

    except Exception as e:
        return {"error": f"Failed to queue workflow: {str(e)}"}


    # Polling loop to check the status
    retries = 0
    output_images = {} # Store output images

    while retries < COMFY_POLLING_MAX_RETRIES:
        try:
            history = get_history(prompt_id)

            # Check if the prompt_id is in the history
            if prompt_id not in history:
                retries += 1
                time.sleep(COMFY_POLLING_INTERVAL_MS / 1000)
                continue

            # Check the status of the workflow based on the history data
            prompt_history = history[prompt_id]

            # Check if there are outputs in the history entry
            if 'outputs' in prompt_history:
                print(f"runpod-worker-comfy - image generation is done")

                # *** ADDED DEBUG LINE HERE ***
                print("--- Listing /comfyui/output contents AFTER workflow execution ---")
                list_directory_contents("/comfyui/output")
                print("--- End listing /comfyui/output ---")

                # Process the outputs to potentially upload to S3 or return base64
                for node_id, node_output in prompt_history['outputs'].items():
                    if 'images' in node_output:
                        for image in node_output['images']:
                            print(f"runpod-worker-comfy - {image['type']}/{image['subfolder']}/{image['filename']}") # Log expected path structure
                            image_path = f"/comfyui/{image['type']}/{image['subfolder']}/{image['filename']}" # Construct full path

                            # Check if file exists before trying to process
                            if os.path.exists(image_path):
                                image_data = base64_encode(image_path) # Encode the existing image
                                output_images[image['filename']] = {"image": image_data} # Return base64
                            else:
                                print(f"runpod-worker-comfy - the image does not exist in the output folder")
                                # Decide how to handle missing files - return error or skip?
                                # Example: Returning an error indicator for this file
                                output_images[image['filename']] = {"error": "Output image file not found after execution."}


                # Prepare the final result dictionary
                result_dict = {
                    "status": "success",
                    "message": "Workflow executed successfully.",
                    "output_images": output_images,
                    "refresh_worker": REFRESH_WORKER,
                }
                
                # *** ADDED DEBUG LINE HERE ***
                print("--- Handler returning final result dictionary: ---")
                print(json.dumps(result_dict, indent=2)) # Print the dict as formatted JSON
                print("--- End handler final result --- ")

                # Return the result with image data
                return result_dict

            # If there are no outputs yet, continue polling
            retries += 1
            time.sleep(COMFY_POLLING_INTERVAL_MS / 1000)

        except Exception as e:
            return {"error": f"An error occurred during polling or processing: {str(e)}"}

    # If the loop completes without finding the result, return a timeout error
    return {"error": "Polling timeout: Workflow execution did not complete within the expected time."}


runpod.serverless.start({"handler": handler})

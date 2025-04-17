import requests # Ensure this is imported
import json     # Ensure this is imported
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app # Ensure these are imported
from flask_login import login_required, current_user

# Assuming your blueprint is named 'views'
views = Blueprint('views', __name__)

# --- Existing Home Route ---
@views.route('/')
def home():
    """Public landing page"""
    return render_template("home.html", user=current_user)

# --- Existing Dashboard Route ---
@views.route('/dashboard')
@login_required
def dashboard():
    """Authenticated user dashboard"""
    # Initialize variables for the template on initial GET request
    return render_template("dashboard.html",
                           user=current_user,
                           ollama_prompt="",
                           ollama_error=None,
                           last_topic="",
                           # NEW: Initialize image variables
                           generated_image_base64=None,
                           image_error=None,
                           last_image_prompt="")

# --- Existing Route for Ollama Text Generation ---
@views.route('/generate_text_prompt', methods=['POST'])
@login_required
def generate_text_prompt():
    generated_prompt = ""
    error_message = None
    user_input_topic = "a futuristic cityscape" # Default topic

    # Form submitted via POST
    if request.method == 'POST':
        user_input_topic = request.form.get('topic', user_input_topic)

    # Construct the prompt for Ollama
    prompt_for_ollama = f"Generate a short, creative visual prompt suitable for an AI image or video generator about: {user_input_topic}"

    # Get Ollama config from app context (set in __init__.py)
    ollama_endpoint = current_app.config['OLLAMA_ENDPOINT']
    ollama_model = current_app.config['OLLAMA_MODEL']

    # Prepare payload for Ollama API
    payload = {
        "model": ollama_model,
        "prompt": prompt_for_ollama,
        "stream": False
    }

    try:
        print(f"Sending request to {ollama_endpoint} with payload: {payload}") # Debug print
        response = requests.post(ollama_endpoint, json=payload, timeout=90) # Increase timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        # Parse JSON response
        data = response.json()
        print(f"Received response from Ollama: {data}") # Debug print
        generated_prompt = data.get('response', '').strip() # Get the text
        if not generated_prompt:
             error_message = "Ollama returned an empty response."

    except requests.exceptions.ConnectionError:
        error_message = f"Error: Could not connect to Ollama service at {ollama_endpoint}. Is it running?"
        print(error_message) # Debug print
    except requests.exceptions.Timeout:
         error_message = "Error: Request to Ollama timed out."
         print(error_message) # Debug print
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        if e.response is not None:
            try: error_detail += f" | Response: {e.response.text}"
            except Exception: pass
        error_message = f"Error contacting Ollama: {error_detail}"
        print(error_message) # Debug print
    except json.JSONDecodeError:
         error_message = "Error: Could not decode response from Ollama."
         print(error_message) # Debug print
    except Exception as e:
         error_message = f"An unexpected error occurred: {e}"
         print(error_message) # Debug print

    # Render the dashboard template again, passing back the results
    return render_template('dashboard.html',
                           user=current_user,
                           ollama_prompt=generated_prompt,
                           ollama_error=error_message,
                           last_topic=user_input_topic,
                           # NEW: Reset image variables when generating text
                           generated_image_base64=None,
                           image_error=None,
                           last_image_prompt=request.form.get('image_prompt', '')) # Keep last image prompt if available

# --- NEW Route for Image Generation ---
@views.route('/generate-image', methods=['POST'])
@login_required
def generate_image():
    # Variables to pass back to the template
    generated_image_b64 = None
    error_message = None
    user_input_prompt = "" # Initialize

    if request.method == 'POST':
        user_input_prompt = request.form.get('image_prompt')
        # Optional: Get other parameters from form
        # negative_prompt = request.form.get('negative_prompt', '')
        # steps = request.form.get('steps', 25, type=int)

        if not user_input_prompt:
            error_message = "Image prompt cannot be empty."
        else:
            # Get Image API URL from app config
            image_api_url = current_app.config['IMAGE_API_URL']
            api_endpoint = f"{image_api_url}/sdapi/v1/txt2img"

            # Prepare payload for AUTOMATIC1111 API
            payload = {
                "prompt": user_input_prompt,
                "steps": 25, # Example default, make configurable if needed
                # "negative_prompt": negative_prompt,
                "width": 512, # Example default
                "height": 512, # Example default
                "sampler_index": "Euler a" # Example default
            }

            try:
                print(f"Sending request to {api_endpoint} with payload: {payload}") # Debug print
                # Increase timeout for image generation
                response = requests.post(api_endpoint, json=payload, timeout=180)
                response.raise_for_status()

                data = response.json()
                images = data.get('images')
                print(f"Received response from Image API: {list(data.keys())}") # Debug: print keys received

                if images and isinstance(images, list) and len(images) > 0:
                    generated_image_b64 = images[0] # Get the first image
                else:
                    error_message = "Image API did not return valid image data."
                    print(f"Invalid image data received: {images}")

            except requests.exceptions.ConnectionError:
                error_message = f"Error: Could not connect to Image Generation service at {image_api_url}. Is it running?"
                print(error_message) # Debug print
            except requests.exceptions.Timeout:
                 error_message = "Error: Image generation request timed out."
                 print(error_message) # Debug print
            except requests.exceptions.RequestException as e:
                error_detail = str(e)
                if e.response is not None:
                    try: error_detail += f" | Response: {e.response.text}"
                    except Exception: pass
                error_message = f"Error contacting Image API: {error_detail}"
                print(error_message) # Debug print
            except json.JSONDecodeError:
                 error_message = "Error: Could not decode response from Image API."
                 print(error_message) # Debug print
            except Exception as e:
                 error_message = f"An unexpected error occurred during image generation: {e}"
                 print(error_message) # Debug print

    # Always render the dashboard template again, passing back the results
    return render_template('dashboard.html',
                           user=current_user,
                           # Pass back Ollama results too (might be empty if just generating image)
                           ollama_prompt=request.form.get('ollama_prompt', ''), # Keep previous if available
                           ollama_error=None,
                           last_topic=request.form.get('topic', ''), # Keep previous if available
                           # Pass back image results
                           generated_image_base64=generated_image_b64,
                           image_error=error_message,
                           last_image_prompt=user_input_prompt) # Pass prompt back to form
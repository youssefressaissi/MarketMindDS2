# flask_app/views.py

import os
import requests
import json
import base64 # For encoding/decoding data
import uuid # For generating unique filenames for image uploads
import traceback # For more detailed error logging
from flask import (Blueprint, render_template, request, flash,
                   redirect, url_for, current_app, session, jsonify)
from flask_login import login_required, current_user
from bson.objectid import ObjectId
from datetime import datetime
from . import mongo # Assuming mongo = PyMongo() initialized in __init__

views = Blueprint('views', __name__)

# --- Constants ---
MAX_HISTORY_MESSAGES = 10
CONVERSATION_TITLE_LENGTH = 40
SUPPORTED_LANGUAGES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "pl": "Polish", "tr": "Turkish", "ru": "Russian", "nl": "Dutch",
    "cs": "Czech", "ar": "Arabic", "zh-cn": "Chinese (Mandarin, simplified)", "hu": "Hungarian",
    "ko": "Korean", "ja": "Japanese"
}
# --- Path to SVD workflow template ---
# Use os.path.join for better cross-platform compatibility
# Assumes 'workflow_templates' is a folder at the same level as your flask_app directory
# Adjust if your structure is different
SVD_WORKFLOW_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'workflow_templates', 'workflow_animated.json'))


# --- System Prompts ---
MARKETING_SYSTEM_PROMPT = """
You are MarketMind, an AI marketing assistant for small business owners.
Your goal is to help entrepreneurs promote their businesses effectively.
Always provide practical marketing advice, content ideas, and growth strategies.
Focus on cost-effective solutions that work well for small businesses with limited resources.
Frame all your responses with marketing and business promotion in mind.
"""

IMAGE_PROMPT_REFINEMENT_SYSTEM_PROMPT = """
You are an expert prompt engineer specializing in creating effective prompts for text-to-image models like Stable Diffusion.
Take the following text, which might be a marketing idea, a description, or a simple user request, and transform it into a concise, descriptive, and visually rich prompt suitable for generating an image.
Focus on keywords, objects, actions, environment, artistic style (e.g., photorealistic, illustration, watercolor, pixel art), composition (e.g., wide shot, close-up), and mood/lighting.
Do not include conversational text, explanations, or apologies in your output. Only output the refined image prompt itself.
"""

# --- Helper Function to get Config ---
def get_config_or_raise(config_key, default=None):
    value = os.environ.get(config_key)
    if not value:
        if default is not None: return default
        error_msg = f"Config Error: Required env var '{config_key}' missing."
        print(f"!!! {error_msg} !!!")
        # In a real app, you might want to raise a more specific exception
        # or handle this more gracefully depending on the context.
        # For now, raising ValueError to make it obvious during development.
        raise ValueError(error_msg)
    return value

# --- Helper Function to Fetch Speakers ---
def get_available_speakers(xtts_api_url_base):
    available_speakers = []
    if not xtts_api_url_base:
        print("WARN: XTTS_API_URL not configured, cannot fetch speakers.")
        return []
    try:
        # Assume common endpoint, verify with your specific XTTS API server docs
        speakers_endpoint = f"{xtts_api_url_base}/speakers_list"
        print(f"DEBUG: Fetching speakers from {speakers_endpoint}")
        # Set a reasonable timeout
        response = requests.get(speakers_endpoint, timeout=15) # Increased timeout slightly
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        speaker_data = response.json()
        # Handle different potential response formats gracefully
        if isinstance(speaker_data, list):
            cleaned_speakers = [str(s).replace('.wav', '') for s in speaker_data if isinstance(s, str)]
            available_speakers.extend(cleaned_speakers)
        elif isinstance(speaker_data, dict):
             # Common case: {"speakers": ["speaker1.wav", ...]}
             if "speakers" in speaker_data and isinstance(speaker_data["speakers"], list):
                 cleaned_speakers = [str(s).replace('.wav', '') for s in speaker_data["speakers"] if isinstance(s, str)]
                 available_speakers.extend(cleaned_speakers)
             # Another possible case: {"speaker1": {...}, "speaker2": {...}}
             else:
                  available_speakers.extend([k for k in speaker_data.keys() if isinstance(k, str)])
        # Filter out any empty strings that might have resulted
        available_speakers = sorted([s for s in available_speakers if s]) # Sort for consistency
        if not available_speakers:
            print("WARN: No speakers returned by XTTS or response format unexpected.")
            print(f"DEBUG: XTTS speakers_list response: {speaker_data}")
        else:
            print(f"DEBUG: Found speakers: {available_speakers}")

    except requests.exceptions.Timeout:
        print(f"WARN: Timeout fetching speakers from {speakers_endpoint}.")
    except requests.exceptions.RequestException as e:
        print(f"WARN: Error fetching speakers: {e}. URL: {speakers_endpoint}")
    except Exception as e:
        # Catch other potential errors like JSONDecodeError
        print(f"WARN: Unexpected error fetching speakers: {type(e).__name__} - {e}.")
    return available_speakers


# --- === ComfyUI SVD Payload Function === ---
def create_svd_payload_from_api_json(init_image_base64):
    """
    Creates the ComfyUI API payload using the workflow template,
    uploads the initial image, and injects the filename.
    """
    print(f"INFO: Creating SVD payload. Image Provided: {'Yes' if init_image_base64 else 'No'}")
    if not init_image_base64:
        print("ERROR: An initial image (base64) is required for the SVD workflow.")
        return None

    try:
        print(f"Attempting to load workflow from calculated path: {SVD_WORKFLOW_FILE}")
        if not os.path.exists(SVD_WORKFLOW_FILE):
            raise FileNotFoundError(f"Workflow file not found at calculated path: {SVD_WORKFLOW_FILE}")

        with open(SVD_WORKFLOW_FILE, "r") as f:
            workflow = json.load(f)
        print("Workflow JSON loaded successfully.")

        # --- Node IDs from YOUR workflow_animated.json ---
        load_image_node_id = "16" # Ensure this ID matches your LoadImage node
        save_node_id = "17"       # Ensure this ID matches your VHS_VideoCombine node
        # ---

        # 1. Upload Initial Image to ComfyUI
        video_api_url_base = get_config_or_raise('VIDEO_API_URL')
        upload_url = f"{video_api_url_base}/upload/image"
        try:
            image_bytes = base64.b64decode(init_image_base64)
        except base64.binascii.Error as decode_error:
            print(f"ERROR: Invalid base64 image data provided: {decode_error}")
            return None
        comfy_image_filename = f"init_svd_{uuid.uuid4().hex[:8]}.png"
        files = {'image': (comfy_image_filename, image_bytes, 'image/png')}

        print(f"Uploading initial image '{comfy_image_filename}' to ComfyUI: {upload_url}")
        upload_response = requests.post(upload_url, files=files, data={"overwrite": "true"}, timeout=45)
        upload_response.raise_for_status()
        upload_data = upload_response.json()
        uploaded_filename = upload_data.get("name")

        if not uploaded_filename:
            print(f"ERROR: ComfyUI image upload failed. Response: {upload_data}")
            return None
        print(f"Image uploaded successfully as: {uploaded_filename}")

        # 2. Inject uploaded filename into the LoadImage node
        if load_image_node_id in workflow:
            if workflow[load_image_node_id].get("class_type") != "LoadImage":
                 print(f"WARN: Node {load_image_node_id} might not be a LoadImage node (class_type: {workflow[load_image_node_id].get('class_type')}).")
            if "inputs" in workflow[load_image_node_id] and "image" in workflow[load_image_node_id]["inputs"]:
                workflow[load_image_node_id]["inputs"]["image"] = uploaded_filename
                print(f"Injected filename '{uploaded_filename}' into LoadImage node {load_image_node_id}")
            else:
                print(f"ERROR: 'inputs' or 'image' key not found on LoadImage node {load_image_node_id}.")
                return None
        else:
            print(f"ERROR: Load Image node ID '{load_image_node_id}' not found in workflow!")
            return None

        # 3. Set filename prefix on the save node
        if save_node_id in workflow:
            if workflow[save_node_id].get("class_type") != "VHS_VideoCombine":
                 print(f"WARN: Node {save_node_id} might not be a VHS_VideoCombine node (class_type: {workflow[save_node_id].get('class_type')}).")
            if "inputs" in workflow[save_node_id] and "filename_prefix" in workflow[save_node_id]["inputs"]:
                workflow[save_node_id]["inputs"]["filename_prefix"] = "marketmind_SVD_output"
                print(f"Set filename_prefix on save node {save_node_id}")
            else:
                print(f"WARN: 'inputs' or 'filename_prefix' key not found on save node {save_node_id}.")
        else:
            print(f"WARN: Save node ID '{save_node_id}' not found.")

        return {"prompt": workflow}

    except FileNotFoundError as e: print(f"ERROR: Workflow file missing: {e}"); return None
    except json.JSONDecodeError as e: print(f"ERROR: Failed to parse workflow JSON! Check {SVD_WORKFLOW_FILE}: {e}"); return None
    except KeyError as e: print(f"ERROR: Key error accessing workflow structure: {e}"); return None
    except requests.exceptions.RequestException as e: print(f"ERROR: Failed to upload image to ComfyUI: {e}"); return None
    except ValueError as e: print(f"ERROR: Value error creating SVD payload: {e}"); return None
    except Exception as e: print(f"ERROR: Unexpected error in create_svd_payload: {type(e).__name__} - {e}\n{traceback.format_exc()}"); return None

# --- Route Utility: Prepare common context ---
def prepare_template_context(user_id_obj, request_data, active_conversation_id_str=None):
    context = {k: v for k, v in request_data.items()}
    context['user'] = current_user
    context.setdefault('last_topic', '')
    context.setdefault('ollama_error', None)
    context.setdefault('image_error', None)
    context.setdefault('last_image_prompt', '')
    context.setdefault('last_refined_prompt', '')
    context.setdefault('generated_image_base64', None)
    # --- Ensure last_init_image_base64 is handled correctly ---
    # Get it from request_data if present, otherwise default to empty string (or None)
    context['last_init_image_base64'] = request_data.get('last_init_image_base64', None)
    if context['last_init_image_base64'] == '': context['last_init_image_base64'] = None # Treat empty string as None
    # ---
    context.setdefault('audio_error', None)
    context.setdefault('last_audio_text', '')
    context.setdefault('last_language_code', 'en')
    context.setdefault('last_speaker_id', None)
    context.setdefault('generated_audio_base64', None)
    context.setdefault('video_error', None)
    context.setdefault('last_video_prompt', '')
    context.setdefault('video_status_message', None)
    context['supported_languages'] = SUPPORTED_LANGUAGES
    context['available_speakers'] = []
    context['all_conversations'] = []
    context['chat_history'] = []
    context['active_conversation_id'] = None

    try:
        if mongo.db is not None:
            context['all_conversations'] = list(mongo.db.conversations.find({"user_id": user_id_obj}).sort("last_updated", -1))
            valid_active_id = None
            if active_conversation_id_str and ObjectId.is_valid(active_conversation_id_str):
                active_convo = mongo.db.conversations.find_one({"_id": ObjectId(active_conversation_id_str), "user_id": user_id_obj})
                if active_convo:
                    valid_active_id = active_conversation_id_str
                    context['chat_history'] = active_convo.get("messages", [])
                else:
                    if 'conversation_id' in request_data: flash("Selected conversation not found.", category='warning')
                    print(f"WARN: Conversation ID {active_conversation_id_str} not found for user {user_id_obj}.")
            context['active_conversation_id'] = valid_active_id
        else:
             print("ERROR: MongoDB connection (mongo.db) is None in prepare_template_context.")
             flash("Database connection error.", category='error')
    except Exception as e:
        print(f"ERROR: Database error fetching conversation data: {e}")
        flash("Error loading conversation data.", category='error')
        context['all_conversations'] = []; context['chat_history'] = []; context['active_conversation_id'] = None

    try:
        xtts_api_url_base = os.environ.get('XTTS_API_URL')
        if xtts_api_url_base:
            context['available_speakers'] = get_available_speakers(xtts_api_url_base)
            # --- Fix: Ensure last_speaker_id from request_data is prioritized ---
            requested_speaker = request_data.get('last_speaker_id')
            if requested_speaker and requested_speaker in context['available_speakers']:
                 context['last_speaker_id'] = requested_speaker
            elif not context.get('last_speaker_id') and context.get('available_speakers'): # Set default only if no speaker was passed in request_data
                 context['last_speaker_id'] = context['available_speakers'][0]
            # If requested speaker is invalid and no default was set, it remains None (or previous value)
        else: print("WARN: XTTS_API_URL environment variable not set. Cannot load speakers.")
    except Exception as e:
        print(f"WARN: Could not get speakers during context preparation: {e}")
        context['available_speakers'] = []

    print(f"DEBUG [prepare_template_context]: active_id={context.get('active_conversation_id')}, last_image_b64_present={bool(context.get('last_init_image_base64'))}, video_status='{context.get('video_status_message')}'")
    return context

# --- Home Route ---
@views.route('/')
def home():
    return render_template("home.html", user=current_user)

# --- Dashboard Route ---
@views.route('/dashboard')
@login_required
def dashboard():
    user_id_obj = ObjectId(current_user.id)
    template_context = prepare_template_context(user_id_obj, request.args, request.args.get('conversation_id'))
    print(f"DEBUG [dashboard route]: Rendering with context. last_init_image_base64_present={bool(template_context.get('last_init_image_base64'))}, video_status='{template_context.get('video_status_message')}'")
    return render_template("dashboard.html", **template_context)

# --- Ollama Text Generation Route ---
@views.route('/generate_text_prompt', methods=['POST'])
@login_required
def generate_text_prompt():
    user_id_obj = ObjectId(current_user.id)
    conversation_id_str = request.form.get('conversation_id')
    user_input_topic = request.form.get('topic', '').strip()
    redirect_state = {k: v for k, v in request.form.items()}
    redirect_state['last_topic'] = user_input_topic

    if not user_input_topic:
        flash("Please enter a topic or message.", category='error')
        return redirect(url_for('views.dashboard', **redirect_state))

    ollama_api_url = None
    try:
        if mongo.db is None: raise ConnectionError("Database unavailable.")
        ollama_endpoint = get_config_or_raise('OLLAMA_ENDPOINT')
        ollama_model = get_config_or_raise('OLLAMA_MODEL')

        conversation_object_id, history = None, []
        if conversation_id_str and ObjectId.is_valid(conversation_id_str):
            conv = mongo.db.conversations.find_one({"_id": ObjectId(conversation_id_str), "user_id": user_id_obj})
            if conv:
                conversation_object_id = conv['_id']; history = conv.get("messages", [])
                print(f"DEBUG: Found existing conversation: {conversation_object_id}")
            else:
                flash("Conversation not found. Starting a new chat.", category='warning')
                conversation_id_str = None
                redirect_state['conversation_id'] = ''
        else:
             print("DEBUG: No valid conversation ID provided, creating new.")
             conversation_id_str = None

        if not conversation_object_id:
            title = user_input_topic[:CONVERSATION_TITLE_LENGTH] + ('...' if len(user_input_topic) > CONVERSATION_TITLE_LENGTH else '')
            new_convo_doc = {"user_id": user_id_obj, "title": title, "created_at": datetime.utcnow(), "last_updated": datetime.utcnow(), "messages": []}
            insert_result = mongo.db.conversations.insert_one(new_convo_doc)
            conversation_object_id = insert_result.inserted_id
            conversation_id_str = str(conversation_object_id)
            history = []
            redirect_state['conversation_id'] = conversation_id_str
            print(f"DEBUG: Created new conversation: {conversation_object_id}")

        messages = [{"role": "system", "content": MARKETING_SYSTEM_PROMPT.strip()}]
        if history:
            valid_history = [{"role": m['role'], "content": m['content']} for m in history[-MAX_HISTORY_MESSAGES:] if m.get('role') and m.get('content')]
            messages.extend(valid_history)
        messages.append({"role": "user", "content": user_input_topic})

        payload = {"model": ollama_model, "messages": messages, "stream": False}
        ollama_api_url = f"{ollama_endpoint}/api/chat"
        print(f"DEBUG: Calling Ollama: {ollama_api_url} with model {ollama_model}")
        response = requests.post(ollama_api_url, json=payload, timeout=90); response.raise_for_status()
        data = response.json(); print(f"DEBUG: Ollama response: {data}")
        latest_ai_response = data.get('message', {}).get('content', '').strip()
        if not latest_ai_response: flash("AI did not provide a response.", category='warning'); print("WARN: Ollama response content was empty.")

        user_message_doc = {"role": "user", "content": user_input_topic, "timestamp": datetime.utcnow()}
        messages_to_save = [user_message_doc]
        if latest_ai_response: messages_to_save.append({"role": "assistant", "content": latest_ai_response, "timestamp": datetime.utcnow()})

        mongo.db.conversations.update_one({"_id": conversation_object_id}, {"$push": {"messages": {"$each": messages_to_save}}, "$set": {"last_updated": datetime.utcnow()}})
        print(f"DEBUG: Saved messages to conversation {conversation_object_id}")

    except requests.exceptions.Timeout: print(f"ERROR: Timeout calling Ollama API at {ollama_api_url}"); flash("Error: The request to the AI text service timed out.", category='error')
    except requests.exceptions.RequestException as e: print(f"ERROR: RequestException calling Ollama API: {e}. URL: {ollama_api_url}"); flash(f"Error connecting to AI text service: {e}", category='error')
    except ValueError as e: print(f"ERROR: Configuration error: {e}"); flash(str(e), category='error')
    except ConnectionError as e: print(f"ERROR: Database connection error: {e}"); flash(str(e), category='error')
    except Exception as e: print(f"ERROR: Unexpected error during text generation: {type(e).__name__} - {e}\n{traceback.format_exc()}"); flash(f"An unexpected error occurred: {e}", category='error')

    # Clear other panel results before redirect
    redirect_state.pop('generated_image_base64', None); redirect_state.pop('image_error', None)
    redirect_state.pop('generated_audio_base64', None); redirect_state.pop('audio_error', None)
    redirect_state.pop('video_status_message', None)
    # Keep last_init_image_base64 in redirect_state

    return redirect(url_for('views.dashboard', **redirect_state))

# --- Image Generation Route ---
@views.route('/generate-image', methods=['POST'])
@login_required
def generate_image():
    user_id_obj = ObjectId(current_user.id)
    user_input_prompt = request.form.get('image_prompt', '').strip()
    # Get image from the SHARED hidden input name
    init_image_b64 = request.form.get('last_init_image_base64')
    if not init_image_b64 or init_image_b64 == 'undefined': init_image_b64 = None
    conversation_id_str = request.form.get('conversation_id')

    # Start with submitted form data
    template_context = {k: v for k, v in request.form.items()}
    # Explicitly set values related to this action
    template_context['last_image_prompt'] = user_input_prompt
    template_context['last_init_image_base64'] = init_image_b64

    generated_image_b64_result = None
    image_gen_error_message = None
    refined_prompt = ""

    try:
        if not conversation_id_str or not ObjectId.is_valid(conversation_id_str): raise ValueError("Cannot generate image without an active conversation.")
        if not user_input_prompt: raise ValueError("Image prompt cannot be empty.")

        ollama_endpoint = get_config_or_raise('OLLAMA_ENDPOINT')
        ollama_model = get_config_or_raise('OLLAMA_MODEL')
        image_api_url_base = get_config_or_raise('IMAGE_API_URL')

        # --- Refine Prompt ---
        print(f"DEBUG: Refining image prompt: '{user_input_prompt}'")
        refinement_payload = {"model": ollama_model,"messages": [{"role": "system", "content": IMAGE_PROMPT_REFINEMENT_SYSTEM_PROMPT.strip()}, {"role": "user", "content": user_input_prompt}],"stream": False }
        refine_response = requests.post(f"{ollama_endpoint}/api/chat", json=refinement_payload, timeout=60); refine_response.raise_for_status()
        refined_prompt = refine_response.json().get('message', {}).get('content', '').strip() or user_input_prompt
        template_context['last_refined_prompt'] = refined_prompt
        print(f"DEBUG: Refined prompt: '{refined_prompt}'")

        # --- Prepare and Call A1111 API ---
        if init_image_b64: # Img2Img
            endpoint = f"{image_api_url_base}/sdapi/v1/img2img"
            payload = {"init_images": [init_image_b64], "prompt": refined_prompt, "negative_prompt": "ugly, deformed, blurry, text, watermark, signature, low quality", "steps": 30, "cfg_scale": 7, "sampler_index": "Euler a", "denoising_strength": 0.7, "seed": -1, "width": 512, "height": 512}
            print(f"DEBUG: Calling A1111 img2img: {endpoint}")
        else: # Text2Img
            endpoint = f"{image_api_url_base}/sdapi/v1/txt2img"
            payload = {"prompt": refined_prompt, "negative_prompt": "ugly, deformed, blurry, text, watermark, signature, low quality", "steps": 25, "cfg_scale": 7, "sampler_index": "Euler a", "seed": -1, "width": 512, "height": 512}
            print(f"DEBUG: Calling A1111 txt2img: {endpoint}")
        payload.setdefault("width", 512); payload.setdefault("height", 512)

        img_response = requests.post(endpoint, json=payload, timeout=180); img_response.raise_for_status()
        response_data = img_response.json()
        images = response_data.get('images')
        if images and images[0]:
            generated_image_b64_result = images[0]
            # --- Update the SHARED state variable in the context ---
            template_context['last_init_image_base64'] = generated_image_b64_result
            print("DEBUG: Image generated successfully.")
        else:
            image_gen_error_message = f"A1111 API returned no image data. Response: {response_data.get('info', response_data)}"
            print(f"WARN: {image_gen_error_message}")

    except requests.exceptions.Timeout: print("ERROR: Timeout calling image generation API."); image_gen_error_message = "Error: The request to the image generation service timed out."
    except requests.exceptions.RequestException as e: print(f"ERROR: RequestException calling image generation API: {e}"); image_gen_error_message = f"Error connecting to image generation service: {e}"
    except ValueError as e: print(f"ERROR: ValueError during image generation: {e}"); image_gen_error_message = str(e)
    except Exception as e: print(f"ERROR: Unexpected error during image generation: {type(e).__name__} - {e}\n{traceback.format_exc()}"); image_gen_error_message = f"An unexpected error occurred: {e}"

    # --- Prepare full context for re-rendering the page ---
    template_context['generated_image_base64'] = generated_image_b64_result
    template_context['image_error'] = image_gen_error_message
    # Clear results from other panels
    template_context.pop('generated_audio_base64', None); template_context['audio_error'] = None
    template_context['video_status_message'] = None

    # Fetch full context needed for the template
    final_render_context = prepare_template_context(user_id_obj, template_context, conversation_id_str)
    return render_template('dashboard.html', **final_render_context)

# --- Audio Generation Route ---
@views.route('/generate-audio', methods=['POST'])
@login_required
def generate_audio():
    user_id_obj = ObjectId(current_user.id)
    text_to_speak = request.form.get('audio_text', '').strip()
    language_code = request.form.get('language_code', 'en')
    speaker_id_from_form = request.form.get('speaker_id')
    conversation_id_str = request.form.get('conversation_id')

    template_context = {k: v for k, v in request.form.items()}
    template_context['last_audio_text'] = text_to_speak
    template_context['last_language_code'] = language_code
    template_context['last_speaker_id'] = speaker_id_from_form

    generated_audio_b64_result = None
    audio_gen_error_message = None
    speaker_id_to_use = None

    try:
        if not conversation_id_str or not ObjectId.is_valid(conversation_id_str): raise ValueError("Cannot generate audio without an active conversation.")
        if not text_to_speak: raise ValueError("Text for audio generation cannot be empty.")
        if language_code not in SUPPORTED_LANGUAGES: raise ValueError(f"Invalid language code selected: {language_code}")

        xtts_api_url_base = get_config_or_raise('XTTS_API_URL', default=None)
        available_speakers = get_available_speakers(xtts_api_url_base)
        template_context['available_speakers'] = available_speakers

        if not available_speakers: raise ValueError("No speakers are available/loaded from the TTS service.")
        if speaker_id_from_form and speaker_id_from_form in available_speakers: speaker_id_to_use = speaker_id_from_form
        elif available_speakers: speaker_id_to_use = available_speakers[0]; print(f"WARN: Speaker '{speaker_id_from_form}' not found, defaulting to '{speaker_id_to_use}'."); flash(f"Selected speaker not available, used default.", category='warning')
        else: raise ValueError("Cannot determine speaker to use.")
        template_context['last_speaker_id'] = speaker_id_to_use

        # --- Prepare and Call XTTS API ---
        xtts_api_endpoint = f"{xtts_api_url_base}/tts_to_audio"
        payload = {"text": text_to_speak, "language": language_code, "speaker_wav": speaker_id_to_use, "options": {}}
        headers = {'Content-Type': 'application/json', 'Accept': 'audio/wav'}
        print(f"DEBUG: Calling XTTS: {xtts_api_endpoint} with lang={language_code}, speaker={speaker_id_to_use}")
        tts_response = requests.post(xtts_api_endpoint, json=payload, headers=headers, timeout=180); tts_response.raise_for_status()

        if 'audio/wav' in tts_response.headers.get('Content-Type', '').lower() and tts_response.content:
            generated_audio_b64_result = base64.b64encode(tts_response.content).decode('utf-8')
            print("DEBUG: Audio generated successfully.")
        else:
            print(f"WARN: XTTS API did not return WAV audio. Status: {tts_response.status_code}, Content-Type: {tts_response.headers.get('Content-Type')}, Response text: {tts_response.text[:200]}")
            audio_gen_error_message = f"XTTS API error (Status {tts_response.status_code}) or unexpected response type."

    except requests.exceptions.Timeout: print("ERROR: Timeout calling audio generation API."); audio_gen_error_message = "Error: The request to the audio generation service timed out."
    except requests.exceptions.RequestException as e: print(f"ERROR: RequestException calling audio generation API: {e}"); audio_gen_error_message = f"Error connecting to audio generation service: {e}"
    except ValueError as e: print(f"ERROR: ValueError during audio generation: {e}"); audio_gen_error_message = str(e)
    except Exception as e: print(f"ERROR: Unexpected error during audio generation: {type(e).__name__} - {e}\n{traceback.format_exc()}"); audio_gen_error_message = f"An unexpected error occurred: {e}"

    # --- Prepare full context for re-rendering the page ---
    template_context['generated_audio_base64'] = generated_audio_b64_result
    template_context['audio_error'] = audio_gen_error_message
    # Clear results from other panels
    template_context.pop('generated_image_base64', None); template_context['image_error'] = None
    template_context['video_status_message'] = None

    final_render_context = prepare_template_context(user_id_obj, template_context, conversation_id_str)
    return render_template('dashboard.html', **final_render_context)

# --- Video Generation Route ---
@views.route('/generate-video', methods=['POST'])
@login_required
def generate_video():
    user_id_obj = ObjectId(current_user.id)
    video_prompt = request.form.get('video_prompt', '').strip() # Get prompt, even if not used by payload
    # Use the name of the SHARED hidden input field
    init_image_b64 = request.form.get('last_init_image_base64')
    if not init_image_b64 or init_image_b64 == 'undefined': init_image_b64 = None
    conversation_id_str = request.form.get('conversation_id')

    # Prepare state for redirect, starting with submitted form data
    redirect_state = {k: v for k, v in request.form.items()}
    # Ensure the image *actually used* for this attempt is preserved
    redirect_state['last_init_image_base64'] = init_image_b64
    # Also preserve the entered video prompt
    redirect_state['last_video_prompt'] = video_prompt

    status_message_for_redirect = None
    video_api_url = None

    try:
        # --- Validation ---
        if not conversation_id_str or not ObjectId.is_valid(conversation_id_str): raise ValueError("Cannot generate video without an active conversation.")
        if not init_image_b64: raise ValueError("Input image required for video generation. Upload/generate one first.")

        # --- Create ComfyUI Payload ---
        print(f"DEBUG [generate_video]: Input image base64 present: {bool(init_image_b64)}")
        comfy_payload = create_svd_payload_from_api_json(init_image_b64)
        if not comfy_payload or not comfy_payload.get("prompt"):
             raise ValueError("Failed to create valid ComfyUI payload. Check logs and workflow configuration.")

        # --- Call ComfyUI API ---
        video_api_url_base = get_config_or_raise('VIDEO_API_URL')
        video_api_url = f"{video_api_url_base}/prompt"
        print(f"*** CALLING COMFYUI (VIDEO) *** -> URL: {video_api_url}")
        response = requests.post(video_api_url, json=comfy_payload, timeout=60); response.raise_for_status()
        response_data = response.json(); prompt_id = response_data.get('prompt_id')
        print(f"DEBUG: ComfyUI Video Queue Response: {response_data}")

        if prompt_id:
            status_message_for_redirect = f"Video generation job submitted (ID: {prompt_id}). Check './output' folder on host after processing."
            print(f"INFO: Video job {prompt_id} submitted.")
        else:
            print(f"ERROR: ComfyUI API call succeeded but did not return a prompt_id. Response: {response_data}")
            status_message_for_redirect = "Error: Video job submitted but could not get Job ID from ComfyUI."

    except requests.exceptions.Timeout: print(f"ERROR: Timeout calling ComfyUI video API at {video_api_url}"); status_message_for_redirect = "Error: The request to the video generation service timed out."
    except requests.exceptions.RequestException as e: print(f"ERROR: RequestException calling ComfyUI video API: {e}. URL: {video_api_url}"); status_message_for_redirect = f"Error connecting to video generation service: {e}"
    except FileNotFoundError as e: print(f"ERROR: {e}"); status_message_for_redirect = f"Configuration Error: Video workflow file not found."
    except ValueError as e: print(f"ERROR: ValueError during video generation setup: {e}"); status_message_for_redirect = str(e) # Show the specific validation error
    except Exception as e: print(f"ERROR: Unexpected error during video generation: {type(e).__name__} - {e}\n{traceback.format_exc()}"); status_message_for_redirect = f"An unexpected error occurred: {e}"

    # --- Prepare state for redirect ---
    redirect_state['video_status_message'] = status_message_for_redirect
    # Clear results from other panels
    redirect_state.pop('generated_image_base64', None); redirect_state.pop('image_error', None)
    redirect_state.pop('generated_audio_base64', None); redirect_state.pop('audio_error', None)

    # --- Redirect back to dashboard ---
    # Pass the full state via keyword arguments using **
    return redirect(url_for('views.dashboard', **redirect_state))
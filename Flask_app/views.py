# flask_app/views.py

import os
import requests
import json
import base64 # For encoding/decoding data
import uuid # For generating unique filenames for image uploads
from flask import (Blueprint, render_template, request, flash,
                   redirect, url_for, current_app, session, jsonify)
from flask_login import login_required, current_user
from bson.objectid import ObjectId
from datetime import datetime
# Assuming mongo is initialized correctly in __init__.py
from . import mongo

views = Blueprint('views', __name__)

# --- Constants ---
MAX_HISTORY_MESSAGES = 1000 # Increased limit, consider pagination for very long chats
CONVERSATION_TITLE_LENGTH = 40
SUPPORTED_LANGUAGES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "pl": "Polish", "tr": "Turkish", "ru": "Russian", "nl": "Dutch",
    "cs": "Czech", "ar": "Arabic", "zh-cn": "Chinese (Mandarin, simplified)", "hu": "Hungarian",
    "ko": "Korean", "ja": "Japanese"
}
# --- Path to your SVD workflow JSON file ---
# Assumes workflow_templates is a sibling folder to your flask_app directory
# or adjust path as needed relative to where Flask runs from.
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
    """Gets a config value from Environment variables or raises ValueError if missing (unless default is provided)."""
    value = os.environ.get(config_key)
    if not value:
        if default is not None:
            print(f"WARN: Environment variable '{config_key}' not set, using default value: '{default}'")
            return default
        error_msg = f"Configuration Error: Required environment variable '{config_key}' is missing or empty."
        print(f"!!! {error_msg} !!!")
        raise ValueError(error_msg)
    return value

# --- Helper Function to Fetch Speakers ---
def get_available_speakers(xtts_api_url_base):
    """Fetches available speaker IDs/filenames from the XTTS service."""
    available_speakers = []
    if not xtts_api_url_base:
        print("WARN: XTTS_API_URL not configured, cannot fetch speakers.")
        return []
    try:

        # !!! CONFIRM this endpoint path from daswer123/xtts-api-server /docs !!!
        # Common endpoints: '/speakers_list', '/speakers', '/list_speakers'
        speakers_endpoint = f"{xtts_api_url_base}/speakers" # Verify this path!

        print(f"DEBUG: Fetching speakers from {speakers_endpoint}")
        response = requests.get(speakers_endpoint, timeout=15)
        response.raise_for_status()

        speaker_data = response.json()
        # Handle various possible response structures
        if isinstance(speaker_data, list):
            cleaned_speakers = [str(s).replace('.wav', '') for s in speaker_data if isinstance(s, str)]
            available_speakers.extend(cleaned_speakers)
        elif isinstance(speaker_data, dict):
            if "speakers" in speaker_data and isinstance(speaker_data["speakers"], list):
                cleaned_speakers = [str(s).replace('.wav', '') for s in speaker_data["speakers"] if isinstance(s, str)]
                available_speakers.extend(cleaned_speakers)
            else:
                 available_speakers.extend([k for k in speaker_data.keys() if isinstance(k, str)])

        available_speakers = sorted([s for s in available_speakers if s])
        if not available_speakers:
            print(f"WARN: No speakers returned by XTTS or response format unexpected. Response: {speaker_data}")
        else:
            print(f"DEBUG: Found speakers: {available_speakers}")

    except requests.exceptions.Timeout: print(f"WARN: Timeout fetching speakers from {speakers_endpoint}.")
    except requests.exceptions.RequestException as e: print(f"WARN: Error fetching speakers: {e}. URL: {speakers_endpoint}")
    except Exception as e: print(f"WARN: Unexpected error fetching/parsing speakers: {type(e).__name__} - {e}.")
    return available_speakers

# --- === ComfyUI SVD Payload Function === ---
def create_svd_payload_from_api_json(init_image_base64):
    """
    Creates the ComfyUI API payload using the SVD workflow template,
    uploads the initial image, and injects the filename.
    """
    print(f"INFO: Creating SVD payload. Image Provided: {'Yes' if init_image_base64 else 'No'}")
    if not init_image_base64:
        print("ERROR: An initial image (base64) is required for the SVD workflow.")
        return None

    try:
        # --- Load the specific workflow JSON ---
        print(f"Attempting to load workflow from: {SVD_WORKFLOW_FILE}")
        if not os.path.exists(SVD_WORKFLOW_FILE):
            raise FileNotFoundError(f"SVD Workflow file not found at specified path: {SVD_WORKFLOW_FILE}")
        with open(SVD_WORKFLOW_FILE, "r") as f:
            workflow = json.load(f)
        print("Workflow JSON loaded successfully.")

        # --- Define Node IDs (MUST MATCH YOUR workflow_animated.json) ---
        load_image_node_id = "16" # The 'LoadImage' node
        save_node_id = "17"       # The 'VHS_VideoCombine' node (or equivalent save node)
        # ---

        # 1. Upload Initial Image to ComfyUI
        video_api_url_base = get_config_or_raise('VIDEO_API_URL') # e.g., http://comfy:8188
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
                 print(f"WARN: Node {load_image_node_id} might not be a LoadImage node.")
            if "inputs" in workflow[load_image_node_id] and "image" in workflow[load_image_node_id]["inputs"]:
                workflow[load_image_node_id]["inputs"]["image"] = uploaded_filename
                print(f"Injected filename '{uploaded_filename}' into LoadImage node {load_image_node_id}")
            else:
                print(f"ERROR: Cannot find 'inputs' or 'image' key in LoadImage node {load_image_node_id} in workflow JSON.")
                return None
        else:
            print(f"ERROR: Load Image node ID '{load_image_node_id}' not found in workflow JSON!")
            return None

        # 3. Set filename prefix on the save node (for organization)
        if save_node_id in workflow:
            if workflow[save_node_id].get("class_type") != "VHS_VideoCombine":
                 print(f"WARN: Node {save_node_id} might not be the expected VHS_VideoCombine node.")
            if "inputs" in workflow[save_node_id] and "filename_prefix" in workflow[save_node_id]["inputs"]:
                workflow[save_node_id]["inputs"]["filename_prefix"] = "marketmind_SVD_output"
                print(f"Set filename_prefix on save node {save_node_id}")
            else:
                print(f"WARN: Cannot find 'inputs' or 'filename_prefix' key on save node {save_node_id}.")
        else:
            print(f"WARN: Save node ID '{save_node_id}' not found in workflow JSON.")

        # The specific SVD workflow likely uses motion prompts implicitly or via other nodes,
        # so we don't explicitly inject the text `video_prompt` here based on workflow_animated.json structure.

        return {"prompt": workflow} # Return structure ComfyUI /prompt endpoint expects

    except FileNotFoundError as e: print(f"ERROR: Workflow file missing: {e}"); return None
    except json.JSONDecodeError as e: print(f"ERROR: Failed to parse workflow JSON! Check file: {e}"); return None
    except KeyError as e: print(f"ERROR: Key error accessing workflow structure: {e}"); return None
    except requests.exceptions.RequestException as e: print(f"ERROR: Failed to upload image to ComfyUI: {e}"); return None
    except ValueError as e: print(f"ERROR: Config or value error creating SVD payload: {e}"); return None
    except Exception as e: print(f"ERROR: Unexpected error in create_svd_payload: {type(e).__name__} - {e}"); return None
# --- End ComfyUI function ---


# --- Route Utility: Prepare common context for dashboard template ---
def prepare_template_context(user_id_obj, request_data, active_conversation_id_str=None):
    """Fetches conversations, speakers, and merges request data for template rendering."""
    context = {k: v for k, v in request_data.items()} # Start with request args/form data
    context['user'] = current_user

    # Set defaults for ALL variables the template might access
    defaults = {
        'last_topic': '', 'ollama_error': None, 'image_error': None,
        'last_image_prompt': '', 'last_refined_prompt': '',
        'generated_image_base64': None, 'last_init_image_base64': None,
        'audio_error': None, 'last_audio_text': '', 'last_language_code': 'en',
        'last_speaker_id': None, 'generated_audio_base64': None,
        'video_error': None, 'last_video_prompt': '', 'video_status_message': None,
        'all_conversations': [], 'chat_history': [], 'active_conversation_id': None,
        'supported_languages': SUPPORTED_LANGUAGES, 'available_speakers': []
    }
    for key, default_value in defaults.items():
        context.setdefault(key, default_value)

    try: # Fetch DB data
        if mongo.db is None: raise ConnectionError("Database connection unavailable.")
        context['all_conversations'] = list(mongo.db.conversations.find({"user_id": user_id_obj}).sort("last_updated", -1))
        valid_active_id = None
        if active_conversation_id_str and ObjectId.is_valid(active_conversation_id_str):
            active_convo = mongo.db.conversations.find_one({"_id": ObjectId(active_conversation_id_str), "user_id": user_id_obj})
            if active_convo:
                valid_active_id = active_conversation_id_str
                context['chat_history'] = active_convo.get("messages", [])
            else:
                if 'conversation_id' in request_data: flash("Selected conversation not found.", category='warning')
        context['active_conversation_id'] = valid_active_id
    except Exception as e:
        print(f"ERROR: DB error fetching context: {e}"); flash("Error loading conversation data.", category='error')
        context['all_conversations'], context['chat_history'], context['active_conversation_id'] = [], [], None

    try: # Fetch speakers
        xtts_api_url_base = os.environ.get('XTTS_API_URL') # Use get with default None
        if xtts_api_url_base:
            context['available_speakers'] = get_available_speakers(xtts_api_url_base)
            # Ensure last_speaker_id is valid or default to first available
            current_speaker = context.get('last_speaker_id')
            if not current_speaker or current_speaker not in context['available_speakers']:
                 context['last_speaker_id'] = context['available_speakers'][0] if context['available_speakers'] else None
        else: print("WARN: XTTS_API_URL not set.")
    except Exception as e: print(f"WARN: Could not get speakers for context: {e}"); context['available_speakers'] = []

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
    print(f"DEBUG: Rendering dashboard. Active Convo ID: {template_context.get('active_conversation_id')}. Video Status: '{template_context.get('video_status_message')}'")
    return render_template("dashboard.html", **template_context)

# --- Ollama Text Generation Route ---
@views.route('/generate_text_prompt', methods=['POST'])
@login_required
def generate_text_prompt():
    print("\n--- Handling POST to /generate_text_prompt ---")
    user_id_obj = ObjectId(current_user.id)
    conversation_id_str = request.form.get('conversation_id')
    user_input_topic = request.form.get('topic', '').strip()

    # Preserve ALL state from the form for the redirect
    redirect_state = {k: v for k, v in request.form.items()}
    redirect_state['last_topic'] = user_input_topic # Ensure current topic is preserved

    if not user_input_topic:
        flash("Please enter a topic or message.", category='error')
        return redirect(url_for('views.dashboard', **redirect_state))

    ollama_api_url = None
    try:
        if mongo.db is None: raise ConnectionError("Database unavailable.")
        ollama_endpoint = get_config_or_raise('OLLAMA_ENDPOINT')
        ollama_model = get_config_or_raise('OLLAMA_MODEL')

        # --- Find or Create Conversation ---
        conversation_object_id, history = None, []
        if conversation_id_str and ObjectId.is_valid(conversation_id_str):
            conv = mongo.db.conversations.find_one({"_id": ObjectId(conversation_id_str), "user_id": user_id_obj})
            if conv: conversation_object_id = conv['_id']; history = conv.get("messages", [])
            else: flash("Conversation not found, starting new.", category='warning'); conversation_id_str = None; redirect_state['conversation_id'] = ''
        if not conversation_object_id:
            title = user_input_topic[:CONVERSATION_TITLE_LENGTH] + ('...' if len(user_input_topic)>CONVERSATION_TITLE_LENGTH else '')
            res = mongo.db.conversations.insert_one({"user_id": user_id_obj, "title": title, "created_at": datetime.utcnow(), "last_updated": datetime.utcnow(), "messages": []})
            conversation_object_id = res.inserted_id; conversation_id_str = str(conversation_object_id); history = []
            redirect_state['conversation_id'] = conversation_id_str # Update state
        # --- End Find/Create ---

        # Prepare messages for Ollama
        messages = [{"role": "system", "content": MARKETING_SYSTEM_PROMPT.strip()}]
        if history: messages.extend([{"role": m['role'], "content": m['content']} for m in history[-MAX_HISTORY_MESSAGES:] if m.get('role') and m.get('content')])
        messages.append({"role": "user", "content": user_input_topic})
        payload = {"model": ollama_model, "messages": messages, "stream": False}
        ollama_api_url = f"{ollama_endpoint}/api/chat"

        # Call Ollama
        print(f"*** CALLING OLLAMA (TEXT) *** -> URL: {ollama_api_url}")
        response = requests.post(ollama_api_url, json=payload, timeout=90); response.raise_for_status()
        data = response.json(); latest_ai_response = data.get('message', {}).get('content', '').strip()
        if not latest_ai_response: flash("AI response empty.", category='warning')

        # Save to DB
        msgs = [{"role": "user", "content": user_input_topic, "timestamp": datetime.utcnow()}]
        if latest_ai_response: msgs.append({"role": "assistant", "content": latest_ai_response, "timestamp": datetime.utcnow()})
        mongo.db.conversations.update_one({"_id": conversation_object_id}, {"$push": {"messages": {"$each": msgs}}, "$set": {"last_updated": datetime.utcnow()}})

    except Exception as e:
        print(f"ERROR text gen: {type(e).__name__} - {e}"); flash(f"Error generating text: {e}", category='error')

    # Clear generated media from other panels before redirect
    redirect_state.pop('generated_image_base64', None); redirect_state.pop('image_error', None)
    redirect_state.pop('generated_audio_base64', None); redirect_state.pop('audio_error', None)
    redirect_state.pop('video_status_message', None)

    return redirect(url_for('views.dashboard', **redirect_state))

# --- Image Generation Route ---
@views.route('/generate-image', methods=['POST'])
@login_required
def generate_image():
    print("\n--- Handling POST to /generate-image ---")
    user_id_obj = ObjectId(current_user.id)
    # Get data from form
    user_input_prompt = request.form.get('image_prompt', '').strip()
    # Use the shared hidden input for the initial image
    init_image_b64 = request.form.get('init_image_base64')
    if not init_image_b64 or init_image_b64 == 'undefined': init_image_b64 = None
    conversation_id_str = request.form.get('conversation_id')

    # Prepare context for rendering, starting with form data
    template_context = {k: v for k, v in request.form.items()}
    template_context['last_image_prompt'] = user_input_prompt
    template_context['last_init_image_base64'] = init_image_b64 # Reflect input used

    # Initialize results
    generated_image_b64_result = None
    image_gen_error_message = None
    refined_prompt = ""
    ollama_api_url = None
    image_api_url_base = None
    endpoint = None

    try:
        # --- Validation & Augment Context ---
        if not conversation_id_str: raise ValueError("No active conversation selected.")
        if not user_input_prompt: raise ValueError("Image prompt cannot be empty.")
        full_context = prepare_template_context(user_id_obj, template_context, conversation_id_str)
        template_context.update(full_context) # Add speakers, history etc.

        # --- Get Config ---
        ollama_endpoint = get_config_or_raise('OLLAMA_ENDPOINT')
        ollama_model = get_config_or_raise('OLLAMA_MODEL')
        image_api_url_base = get_config_or_raise('IMAGE_API_URL') # A1111 URL

        # --- Refine Prompt ---
        print("--- Step 1: Refining prompt ---")
        refinement_payload = {"model": ollama_model,"messages": [{"role": "system", "content": IMAGE_PROMPT_REFINEMENT_SYSTEM_PROMPT.strip()}, {"role": "user", "content": user_input_prompt}],"stream": False }
        ollama_api_url = f"{ollama_endpoint}/api/chat"
        print(f"*** CALLING OLLAMA (REFINE) *** -> URL: {ollama_api_url}")
        refine_response = requests.post(ollama_api_url, json=refinement_payload, timeout=60); refine_response.raise_for_status()
        refined_prompt = refine_response.json().get('message', {}).get('content', '').strip() or user_input_prompt
        template_context['last_refined_prompt'] = refined_prompt # Update context
        print(f"Refined prompt: '{refined_prompt}'")

        # --- Prepare A1111 API Call (Handles Img2Img vs Text2Img) ---
        print(f"--- Step 2: Calling A1111 --- (Image provided: {'Yes' if init_image_b64 else 'No'})")
        if init_image_b64:
            endpoint = f"{image_api_url_base}/sdapi/v1/img2img"
            payload = {
                "init_images": [init_image_b64],
                "prompt": refined_prompt,
                "negative_prompt": "ugly, deformed, blurry, text, watermark, signature, low quality, words",
                "steps": 30, "cfg_scale": 7, "sampler_index": "Euler a",
                "denoising_strength": 0.7, "seed": -1,
                "width": 512, "height": 512 # Consider getting size from init_image if possible
            }
            print(f"Using img2img endpoint: {endpoint}")
        else:
            endpoint = f"{image_api_url_base}/sdapi/v1/txt2img"
            payload = {
                "prompt": refined_prompt,
                "negative_prompt": "ugly, deformed, blurry, text, watermark, signature, low quality, words",
                "steps": 25, "cfg_scale": 7, "sampler_index": "Euler a",
                "seed": -1, "width": 512, "height": 512
            }
            print(f"Using txt2img endpoint: {endpoint}")

        payload.setdefault("width", 512); payload.setdefault("height", 512) # Ensure defaults

        # --- Call A1111 ---
        print(f"*** CALLING A1111 (IMAGE) *** -> URL: {endpoint}")
        img_response = requests.post(endpoint, json=payload, timeout=180); img_response.raise_for_status()
        response_data = img_response.json(); images = response_data.get('images')
        if images and images[0]:
            generated_image_b64_result = images[0]
            # Update the shared state with the NEWLY generated image
            template_context['last_init_image_base64'] = generated_image_b64_result
            print("Image generated successfully.")
        else:
            image_gen_error_message = f"A1111 API returned no image data. Info: {response_data.get('info', response_data)}"
            print(f"WARN: {image_gen_error_message}")

    # --- Error Handling ---
    except ValueError as e: print(f"ERROR: {e}"); image_gen_error_message = str(e)
    except ConnectionError as e: error_msg=f"DB Error: {e}"; image_gen_error_message = error_msg; print(error_msg)
    except requests.exceptions.RequestException as e:
        failed_url = ollama_api_url if 'ollama' in str(e).lower() else (endpoint if endpoint else image_api_url_base)
        error_msg = f"API Connection Error: Could not connect to service at {failed_url}. {e}"
        image_gen_error_message = error_msg; print(error_msg)
    except requests.exceptions.Timeout as e:
        error_msg = f"API Timeout Error: Request to service timed out. {e}"
        image_gen_error_message = error_msg; print(error_msg)
    except Exception as e:
        print(f"!! UNEXPECTED Error in generate_image: {type(e).__name__} - {e}")
        image_gen_error_message = f"An unexpected error occurred: {e}"

    # --- Update context with results for rendering ---
    template_context['generated_image_base64'] = generated_image_b64_result
    template_context['image_error'] = image_gen_error_message
    # Clear other panel results
    template_context.pop('generated_audio_base64', None); template_context['audio_error'] = None
    template_context['video_status_message'] = None

    print("Rendering dashboard after image generation attempt.")
    return render_template('dashboard.html', **template_context)

# --- Audio Generation Route ---
@views.route('/generate-audio', methods=['POST'])
@login_required
def generate_audio():
    print("\n--- Handling POST to /generate-audio ---")
    user_id_obj = ObjectId(current_user.id)
    # Get data from form
    text_to_speak = request.form.get('audio_text', '').strip()
    language_code = request.form.get('language_code', 'en')
    speaker_id_from_form = request.form.get('speaker_id')
    conversation_id_str = request.form.get('conversation_id')

    # Prepare context for rendering, starting with form data
    template_context = {k: v for k, v in request.form.items()}
    template_context['last_audio_text'] = text_to_speak
    template_context['last_language_code'] = language_code
    template_context['last_speaker_id'] = speaker_id_from_form

    # Initialize results
    generated_audio_b64_result = None
    audio_gen_error_message = None
    speaker_id_to_use = None
    xtts_api_url_base = None
    xtts_api_endpoint = None

    try:
        # --- Validation & Augment Context ---
        if not conversation_id_str: raise ValueError("No active conversation selected.")
        if not text_to_speak: raise ValueError("Text for audio cannot be empty.")
        if language_code not in SUPPORTED_LANGUAGES: raise ValueError(f"Invalid language: {language_code}")
        full_context = prepare_template_context(user_id_obj, template_context, conversation_id_str)
        template_context.update(full_context) # Add speakers, history etc.

        # --- Determine Speaker ---
        available_speakers = template_context.get('available_speakers', [])
        if not available_speakers: raise ValueError("No speakers loaded from TTS service.")
        if speaker_id_from_form and speaker_id_from_form in available_speakers:
            speaker_id_to_use = speaker_id_from_form
        else:
            speaker_id_to_use = available_speakers[0] # Default to first
            if speaker_id_from_form: flash(f"Speaker '{speaker_id_from_form}' not found, using default.", category='warning')
        template_context['last_speaker_id'] = speaker_id_to_use # Update context with actual used speaker

        # --- Get Config & Prepare API Call ---
        xtts_api_url_base = get_config_or_raise('XTTS_API_URL')
        xtts_api_endpoint = f"{xtts_api_url_base}/tts_to_audio" # Verify endpoint
        payload = {"text": text_to_speak, "language": language_code, "speaker_wav": speaker_id_to_use, "options": {}}
        headers = {'Content-Type': 'application/json', 'Accept': 'audio/wav'}
        print(f"*** CALLING XTTS *** -> URL: {xtts_api_endpoint} | Lang: {language_code} | Speaker: {speaker_id_to_use}")

        # --- Call XTTS API ---
        tts_response = requests.post(xtts_api_endpoint, json=payload, headers=headers, timeout=180)
        tts_response.raise_for_status() # Check HTTP errors

        # --- Process Response ---
        content_type = tts_response.headers.get('Content-Type', '').lower()
        if 'audio/wav' in content_type and tts_response.content:
            generated_audio_b64_result = base64.b64encode(tts_response.content).decode('utf-8')
            print("Audio generated successfully.")
        else:
            error_detail = tts_response.text[:500] if tts_response.text else "(Empty Response Body)"
            audio_gen_error_message = f"XTTS API Error: Status {tts_response.status_code}, Content-Type '{content_type}'. Response: {error_detail}"
            print(f"WARN: {audio_gen_error_message}")

    # --- Error Handling ---
    except ValueError as e: print(f"ERROR: {e}"); audio_gen_error_message = str(e)
    except ConnectionError as e: error_msg=f"DB Error: {e}"; audio_gen_error_message = error_msg; print(error_msg)
    except requests.exceptions.RequestException as e:
        error_msg = f"API Connection Error: Could not connect to TTS service at {xtts_api_url_base}. {e}"
        audio_gen_error_message = error_msg; print(error_msg)
    except requests.exceptions.Timeout as e:
        error_msg = f"API Timeout Error: Request to TTS service timed out. {e}"
        audio_gen_error_message = error_msg; print(error_msg)
    except Exception as e:
        print(f"!! UNEXPECTED Error in generate_audio: {type(e).__name__} - {e}")
        audio_gen_error_message = f"An unexpected error occurred: {e}"

    # --- Update context with results for rendering ---
    template_context['generated_audio_base64'] = generated_audio_b64_result
    template_context['audio_error'] = audio_gen_error_message
    # Clear other panel results
    template_context.pop('generated_image_base64', None); template_context['image_error'] = None
    template_context['video_status_message'] = None

    print("Rendering dashboard after audio generation attempt.")
    return render_template('dashboard.html', **template_context)

# --- Video Generation Route ---
@views.route('/generate-video', methods=['POST'])
@login_required
def generate_video():
    print("\n--- Handling POST to /generate-video ---")
    user_id_obj = ObjectId(current_user.id)
    # Get data from form
    video_prompt = request.form.get('video_prompt', '').strip() # Motion prompt
    init_image_b64 = request.form.get('last_init_image_base64') # Get image from SHARED state input
    if not init_image_b64 or init_image_b64 == 'undefined': init_image_b64 = None
    conversation_id_str = request.form.get('conversation_id')

    # Preserve ALL state from the form for the redirect
    redirect_state = {k: v for k, v in request.form.items()}
    redirect_state['last_video_prompt'] = video_prompt
    redirect_state['last_init_image_base64'] = init_image_b64 # Pass back the image used

    status_message_for_redirect = None
    video_api_url = None

    try:
        # --- Validation ---
        if not conversation_id_str: raise ValueError("No active conversation selected.")
        if not init_image_b64: raise ValueError("Input image required for video generation.")
        # if not video_prompt: raise ValueError("Video motion prompt cannot be empty.") # Optional validation

        # --- Create ComfyUI Payload ---
        print("--- Step 1: Creating ComfyUI SVD Payload ---")
        comfy_payload = create_svd_payload_from_api_json(init_image_b64)
        if not comfy_payload or not comfy_payload.get("prompt"):
             raise ValueError("Failed to create valid ComfyUI payload (Check logs, workflow JSON path & Node IDs).")

        # --- Call ComfyUI API ---
        print("--- Step 2: Calling ComfyUI Video API ---")
        video_api_url_base = get_config_or_raise('VIDEO_API_URL') # e.g., http://comfy:8188
        video_api_url = f"{video_api_url_base}/prompt"
        print(f"*** CALLING COMFYUI (VIDEO) *** -> URL: {video_api_url}")
        response = requests.post(video_api_url, json=comfy_payload, timeout=60)
        response.raise_for_status()
        response_data = response.json(); prompt_id = response_data.get('prompt_id')
        print(f"DEBUG: ComfyUI Video Queue Response: {response_data}")

        if prompt_id:
            status_message_for_redirect = f"Video job submitted! (ID: {prompt_id}). Check './output'."
            print(f"INFO: Video job {prompt_id} submitted.")
        else:
            status_message_for_redirect = "Error: ComfyUI didn't return job ID."
            print(f"ERROR: ComfyUI call succeeded but no prompt_id returned. Response: {response_data}")

    # --- Error Handling ---
    except ValueError as e: print(f"ERROR: {e}"); status_message_for_redirect = str(e)
    except ConnectionError as e: error_msg=f"DB Error: {e}"; status_message_for_redirect = error_msg; print(error_msg)
    except FileNotFoundError as e: print(f"ERROR: {e}"); status_message_for_redirect = f"Config Error: Video workflow file not found."
    except requests.exceptions.RequestException as e:
        error_msg = f"API Connection Error: Could not connect to Video service at {video_api_url or get_config_or_raise('VIDEO_API_URL','?')}. {e}"
        status_message_for_redirect = error_msg; print(error_msg)
    except requests.exceptions.Timeout as e:
        error_msg = f"API Timeout Error: Request to Video service timed out. {e}"
        status_message_for_redirect = error_msg; print(error_msg)
    except Exception as e:
        print(f"!! UNEXPECTED Error in generate_video: {type(e).__name__} - {e}")
        status_message_for_redirect = f"An unexpected error occurred: {e}"

    # Set status message for redirect state
    redirect_state['video_status_message'] = status_message_for_redirect
    # Clear other panel results before redirect
    redirect_state.pop('generated_image_base64', None); redirect_state.pop('image_error', None)
    redirect_state.pop('generated_audio_base64', None); redirect_state.pop('audio_error', None)

    # --- FIX: Redirect using **redirect_state to pass all keyword arguments ---
    print(f"Redirecting to dashboard after video gen attempt with conversation_id: {conversation_id_str}")
    return redirect(url_for('views.dashboard', **redirect_state))
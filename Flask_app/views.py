# --- flask_app/views.py ---

import os
import requests
import json
import base64 # For encoding audio data
from flask import (Blueprint, render_template, request, flash,
                   redirect, url_for, current_app, session, jsonify)
from flask_login import login_required, current_user
from bson.objectid import ObjectId
from datetime import datetime
# Assuming mongo is initialized correctly in __init__.py
from . import mongo

views = Blueprint('views', __name__)

# --- Constants ---
MAX_HISTORY_MESSAGES = 10
CONVERSATION_TITLE_LENGTH = 40
SUPPORTED_LANGUAGES = { # From old file - Needed for Audio
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "pl": "Polish", "tr": "Turkish", "ru": "Russian", "nl": "Dutch",
    "cs": "Czech", "ar": "Arabic", "zh-cn": "Chinese (Mandarin, simplified)", "hu": "Hungarian",
    "ko": "Korean", "ja": "Japanese"
}

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
def get_config_or_raise(config_key):
    """Gets a config value from Environment variables or raises ValueError if missing."""
    value = os.environ.get(config_key)
    if not value:
        error_msg = f"Configuration Error: Required environment variable '{config_key}' is missing or empty."
        print(f"!!! {error_msg} !!!")
        raise ValueError(error_msg)
    # print(f"DEBUG: Read ENV VAR {config_key} = {value}") # Keep for debugging if needed
    return value

# --- Helper Function to Fetch Speakers (From old file - review endpoint/parsing) ---
def get_available_speakers(xtts_api_url_base):
    """Fetches available speaker IDs/filenames from the XTTS service."""
    available_speakers = [] # Start with an empty list
    try:
        # !!! CONFIRM this endpoint path from daswer123/xtts-api-server /docs !!!
        # Common endpoints: '/speakers_list', '/speakers', '/list_speakers'
        speakers_endpoint = f"{xtts_api_url_base}/speakers_list" # Verify this path!
        print(f"DEBUG: Fetching speakers from {speakers_endpoint}")
        response = requests.get(speakers_endpoint, timeout=10) # 10 second timeout

        if response.status_code == 200:
            speaker_data = response.json()
            # --- Adapt parsing based on ACTUAL response structure from the API ---
            if isinstance(speaker_data, list):
                # Assumes response is like ["speaker1.wav", "speaker2.wav"]
                cleaned_speakers = [str(s).replace('.wav', '') for s in speaker_data if isinstance(s, str)]
                available_speakers.extend(cleaned_speakers)
            elif isinstance(speaker_data, dict) and "speakers" in speaker_data and isinstance(speaker_data["speakers"], list):
                # Assumes response is like {"speakers": ["speaker1", "speaker2"]}
                cleaned_speakers = [str(s).replace('.wav', '') for s in speaker_data["speakers"] if isinstance(s, str)]
                available_speakers.extend(cleaned_speakers)
            elif isinstance(speaker_data, dict):
                 # Assumes response is like {"speaker1": "path/to/1.wav", "speaker2": "path/to/2.wav"}
                 available_speakers.extend(list(speaker_data.keys())) # Use keys as speaker IDs

            # Filter out any empty strings just in case
            available_speakers = [s for s in available_speakers if s]
            print(f"DEBUG: Found speakers: {available_speakers}")
            if not available_speakers:
                 print("WARN: No speakers returned by XTTS service endpoint (or parsing failed). Check XTTS logs and `./xtts/actors`.")
        else:
            print(f"WARN: Could not fetch speakers (Status: {response.status_code}). Response: {response.text[:200]}")
    except requests.exceptions.Timeout:
         print(f"WARN: Timeout fetching speakers from {speakers_endpoint}.")
    except requests.exceptions.RequestException as e:
        print(f"WARN: Error fetching speakers from XTTS service at {xtts_api_url_base}: {e}.")
    except Exception as e: # Catch JSONDecodeError etc.
        print(f"WARN: Unexpected error fetching/parsing speakers: {e}.")

    return available_speakers # Return list (might be empty)

# --- Home Route ---
@views.route('/')
def home():
    return render_template("home.html", user=current_user)

# --- Dashboard Route (MERGED) ---
@views.route('/dashboard')
@login_required
def dashboard():
    user_id_obj = ObjectId(current_user.id)
    print(f"\n--- Loading Dashboard for user: {user_id_obj} ---")
    all_conversations = []
    active_conversation_id_str = request.args.get('conversation_id')
    active_conversation = None
    active_chat_history = []
    available_speakers = [] # Initialize empty - For Audio Panel
    last_speaker_id_default = None # Initialize - For Audio Panel

    try:
        # --- Fetch speakers (from old dashboard logic) ---
        try:
            xtts_api_url_base = get_config_or_raise('XTTS_API_URL')
            available_speakers = get_available_speakers(xtts_api_url_base)
            if available_speakers:
                last_speaker_id_default = available_speakers[0] # Default to first speaker
        except ValueError as e:
            flash(f"Audio service configuration error: {e}", category='error')
        except Exception as e:
            print(f"WARN: Could not fetch speakers during dashboard load: {e}")
            flash("Could not load speaker list from audio service.", category='warning')
        # --- End speaker fetching ---

        # --- Fetch conversations (from working dashboard logic) ---
        if mongo.db is None: # <-- CORRECTED CHECK
            print("ERROR: MongoDB connection object (mongo.db) not available for dashboard load.")
            flash("Database connection error.", category='error')
        else:
            all_conversations_cursor = mongo.db.conversations.find({
                "user_id": user_id_obj
            }).sort("last_updated", -1)
            all_conversations = list(all_conversations_cursor)
            print(f"Found {len(all_conversations)} conversations for sidebar.")

            # Load active conversation if ID is valid
            if active_conversation_id_str and ObjectId.is_valid(active_conversation_id_str):
                print(f"Attempting to load specified conversation: {active_conversation_id_str}")
                active_conversation_id_obj = ObjectId(active_conversation_id_str)
                active_conversation = mongo.db.conversations.find_one({
                    "_id": active_conversation_id_obj,
                    "user_id": user_id_obj
                })
                if active_conversation:
                    print("Loaded specified conversation successfully.")
                    active_chat_history = active_conversation.get("messages", [])
                else:
                    print("Specified conversation not found or doesn't belong to user.")
                    flash("Selected conversation not found.", category='warning')
                    active_conversation_id_str = None
        # --- End conversation fetching ---

    except Exception as e:
        print(f"Error loading dashboard data: {e}")
        flash("Error loading dashboard data.", category='error')
        all_conversations = []
        active_conversation_id_str = None
        active_chat_history = []
        available_speakers = [] # Ensure empty on error
        last_speaker_id_default = None

    print(f"Rendering dashboard with active_conversation_id: {active_conversation_id_str}, history length: {len(active_chat_history)}")
    # Pass ALL necessary variables for all panels
    return render_template("dashboard.html",
                           user=current_user,
                           all_conversations=all_conversations,
                           active_conversation_id=active_conversation_id_str,
                           chat_history=active_chat_history,
                           # Text panel defaults
                           ollama_prompt="", ollama_error=None, last_topic="",
                           # Image panel defaults
                           image_error=None, last_image_prompt="", last_refined_prompt="",
                           generated_image_base64=None,
                           # Audio panel defaults/data
                           supported_languages=SUPPORTED_LANGUAGES, # From old file
                           available_speakers=available_speakers, # From old file (fetched above)
                           generated_audio_base64=None,
                           audio_error=None, last_audio_text="", last_language_code="en",
                           last_speaker_id=last_speaker_id_default # From old file (determined above)
                           )


# --- Ollama Text Generation Route (MERGED) ---
@views.route('/generate_text_prompt', methods=['POST'])
@login_required
def generate_text_prompt():
    print("\n--- Handling POST to /generate_text_prompt ---")
    user_id_obj = ObjectId(current_user.id)
    conversation_id_str = request.form.get('conversation_id')
    user_input_topic = request.form.get('topic') # Renamed from ollama_prompt for clarity

    # Preserve state from other panels for re-rendering (from old file)
    form_state = {
        'last_image_prompt': request.form.get('image_prompt', ''),
        'last_refined_prompt': request.form.get('last_refined_prompt', ''),
        'generated_image_base64': request.form.get('generated_image_base64'),
        'last_audio_text': request.form.get('audio_text', ''),
        'last_language_code': request.form.get('language_code', 'en'),
        'last_speaker_id': request.form.get('speaker_id'),
        'generated_audio_base64': request.form.get('generated_audio_base64'),
        'last_topic': user_input_topic # Pass back the topic attempted
    }

    print(f"Received form data - user: {user_id_obj}, conversation_id: {conversation_id_str}, topic: {user_input_topic}")

    if not user_input_topic:
        flash("Please enter a topic or message.", category='error')
        # Redirect, preserving conversation ID and *other* form state
        redirect_url = url_for('views.dashboard', conversation_id=conversation_id_str, **form_state) if conversation_id_str else url_for('views.dashboard', **form_state)
        return redirect(redirect_url)

    conversation_object_id = None
    conversation_history = []
    ollama_endpoint = None
    latest_ai_response = ""

    try:
        # Corrected DB Check
        if mongo.db is None:
            raise ConnectionError("Database connection not available.")

        ollama_endpoint = get_config_or_raise('OLLAMA_ENDPOINT')
        ollama_model = get_config_or_raise('OLLAMA_MODEL')

        # Find or Create Conversation (from working file)
        if conversation_id_str and ObjectId.is_valid(conversation_id_str):
            conversation_object_id = ObjectId(conversation_id_str)
            conversation = mongo.db.conversations.find_one({"_id": conversation_object_id, "user_id": user_id_obj})
            if conversation:
                print(f"Found existing conversation: {conversation_object_id}")
                conversation_history = conversation.get("messages", [])
            else:
                print(f"Conversation ID {conversation_id_str} invalid/not found for user {user_id_obj}. Creating new.")
                conversation_id_str = None; conversation_object_id = None; conversation_history = []
        else:
            conversation_history = [] # Ensure empty if no valid ID provided

        if not conversation_object_id:
            title = user_input_topic[:CONVERSATION_TITLE_LENGTH] + ('...' if len(user_input_topic) > CONVERSATION_TITLE_LENGTH else '')
            new_convo_doc = {"user_id": user_id_obj, "title": title, "created_at": datetime.utcnow(), "last_updated": datetime.utcnow(), "messages": []}
            insert_result = mongo.db.conversations.insert_one(new_convo_doc)
            conversation_object_id = insert_result.inserted_id
            conversation_id_str = str(conversation_object_id)
            print(f"Created new conversation with ID: {conversation_id_str}, Title: '{title}'")

        # Prepare Prompt (from working file)
        user_message_content = user_input_topic
        messages_for_ollama = [{"role": "system", "content": MARKETING_SYSTEM_PROMPT.strip()}]
        if conversation_history:
            recent_history = conversation_history[-MAX_HISTORY_MESSAGES:]
            for msg in recent_history:
                 if msg.get('role') and msg.get('content'):
                    messages_for_ollama.append({"role": msg['role'], "content": msg['content']})
        messages_for_ollama.append({"role": "user", "content": user_message_content})
        print(f"--- Messages prepared for Ollama API (count: {len(messages_for_ollama)}) ---")

        # Call Ollama API (from working file, using correct endpoint)
        payload = {"model": ollama_model, "messages": messages_for_ollama, "stream": False}
        ollama_api_url = f"{ollama_endpoint}/api/chat" # Use the correct endpoint path
        print(f"*** CALLING OLLAMA (TEXT) *** -> URL: {ollama_api_url}")
        response = requests.post(ollama_api_url, json=payload, timeout=90)
        response.raise_for_status()
        data = response.json()
        latest_ai_response = data.get('message', {}).get('content', '').strip()

        if not latest_ai_response:
            print("Ollama returned empty response content.")
            flash("AI response was empty.", category='warning')
        else:
            print(f"Ollama response received: {latest_ai_response[:100]}...")

        # Save interaction to DB (from working file)
        user_msg_doc = {"role": "user", "content": user_message_content, "timestamp": datetime.utcnow()}
        messages_to_save = [user_msg_doc]
        if latest_ai_response:
            ai_msg_doc = {"role": "assistant", "content": latest_ai_response, "timestamp": datetime.utcnow()}
            messages_to_save.append(ai_msg_doc)
        print(f"Saving {len(messages_to_save)} messages to conversation ID: {conversation_object_id}")
        mongo.db.conversations.update_one({"_id": conversation_object_id},{"$push": {"messages": {"$each": messages_to_save}}, "$set": {"last_updated": datetime.utcnow()}})

    # Error Handling (Combined and refined)
    except ValueError as e: print(f"Error in generate_text_prompt: {e}"); flash(str(e), category='error')
    except ConnectionError as e: error_msg = f"Error: Could not connect to database. {e}"; flash(error_msg, category='error'); print(error_msg)
    except requests.exceptions.ConnectionError as e: error_msg = f"Error: Could not connect to Ollama at {ollama_endpoint}. Ensure the service is running."; flash(error_msg, category='error'); print(error_msg)
    except requests.exceptions.Timeout: error_msg = f"Error: Request to Ollama ({ollama_api_url}) timed out."; flash(error_msg, category='error'); print(error_msg)
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else 'N/A'
        response_text = e.response.text[:200] if e.response is not None else str(e)
        error_msg = f"Ollama API Error ({status_code}) from {ollama_api_url}: {response_text}"
        flash(error_msg, category='error'); print(error_msg)
    except Exception as e:
        error_msg = f"An unexpected error occurred during text generation: {e}"
        flash(error_msg, category='error'); print(f"!! UNEXPECTED ERROR: {e}")

    # Redirect back to the dashboard, passing the form state
    # Note: latest_ai_response is NOT directly passed back here,
    # it will be loaded from chat_history when the dashboard reloads.
    redirect_url = url_for('views.dashboard', conversation_id=conversation_id_str, **form_state)
    print(f"Redirecting to dashboard with conversation_id: {conversation_id_str}")
    return redirect(redirect_url)


# --- Image Generation Route (MERGED) ---
@views.route('/generate-image', methods=['POST'])
@login_required
def generate_image():
    print("\n--- Handling POST to /generate-image ---")
    user_id_obj = ObjectId(current_user.id)

    # --- Get initial form data ---
    user_input_prompt = request.form.get('image_prompt')
    conversation_id_str = request.form.get('conversation_id')
    # Preserve state from other panels
    last_topic = request.form.get('topic', '')
    last_audio_text = request.form.get('audio_text', '')
    last_language_code = request.form.get('language_code', 'en')
    last_speaker_id = request.form.get('speaker_id')
    generated_audio_base64 = request.form.get('generated_audio_base64') # Preserve generated audio

    print(f"Received form data - user: {user_id_obj}, conversation_id: {conversation_id_str}, initial_image_prompt: {user_input_prompt}")

    # --- Variables needed for rendering template ---
    generated_image_b64 = None
    image_gen_error_message = None
    refined_prompt_for_image = ""
    all_conversations = []
    chat_history = []
    available_speakers = [] # Fetch for re-render - From old file
    ollama_endpoint = None
    image_api_url = None

    try:
        # Corrected DB Check
        if mongo.db is None:
            raise ConnectionError("Database connection not available.")

        # --- Fetch context needed for re-rendering template ---
        # Fetch speakers (from old file)
        try:
            xtts_api_url_base = get_config_or_raise('XTTS_API_URL')
            available_speakers = get_available_speakers(xtts_api_url_base)
            # Determine default/current speaker for template
            if not last_speaker_id or last_speaker_id not in available_speakers:
                last_speaker_id = available_speakers[0] if available_speakers else None
        except ValueError as e: flash(f"Audio service config error: {e}", category='error') # Notify but don't stop image gen
        except Exception as e: print(f"WARN: Could not fetch speakers for image route render: {e}")

        # Fetch conversations and history (from working file)
        all_conversations = list(mongo.db.conversations.find({"user_id": user_id_obj}).sort("last_updated", -1))
        if conversation_id_str and ObjectId.is_valid(conversation_id_str):
            conversation = mongo.db.conversations.find_one({"_id": ObjectId(conversation_id_str), "user_id": user_id_obj})
            if conversation: chat_history = conversation.get("messages", [])
            else: conversation_id_str = None; chat_history = [] # Reset if convo not found
        # --- End fetching context ---

        # --- Get Config Needed for Image Gen ---
        ollama_endpoint = get_config_or_raise('OLLAMA_ENDPOINT')
        ollama_model = get_config_or_raise('OLLAMA_MODEL')
        image_api_url = get_config_or_raise('IMAGE_API_URL') # Base URL for A1111
        # --- End Config Check ---

        if not user_input_prompt:
            raise ValueError("Image prompt cannot be empty.")

        # === Step 1: Refine prompt using Ollama ===
        print(f"--- Step 1: Refining prompt using Ollama ---")
        refinement_payload = {"model": ollama_model,"messages": [{"role": "system", "content": IMAGE_PROMPT_REFINEMENT_SYSTEM_PROMPT.strip()}, {"role": "user", "content": user_input_prompt}],"stream": False }
        ollama_api_url = f"{ollama_endpoint}/api/chat" # Use correct endpoint
        print(f"*** CALLING OLLAMA (REFINE) *** -> URL: {ollama_api_url}")
        refine_response = requests.post(ollama_api_url, json=refinement_payload, timeout=60)
        refine_response.raise_for_status()
        refine_data = refine_response.json()
        refined_prompt_for_image = refine_data.get('message', {}).get('content', '').strip()

        if not refined_prompt_for_image:
            print("Ollama refinement returned empty response, falling back to original prompt.")
            refined_prompt_for_image = user_input_prompt
        else:
             print(f"Refined prompt received: {refined_prompt_for_image}")

        # === Step 2: Call AUTOMATIC1111 API ===
        print(f"--- Step 2: Generating image using AUTOMATIC1111 ---")
        image_api_endpoint = f"{image_api_url}/sdapi/v1/txt2img" # A1111 endpoint
        image_payload = {
            "prompt": refined_prompt_for_image,
            "steps": 25, "width": 512, "height": 512, "sampler_index": "Euler a",
            "negative_prompt": "ugly, deformed, blurry, low quality, text, words, signature, watermark, username, person, people"
        }
        print(f"*** CALLING A1111 (IMAGE) *** -> URL: {image_api_endpoint}")
        print(f"Image Payload Prompt: {image_payload['prompt'][:100]}...")
        image_response = requests.post(image_api_endpoint, json=image_payload, timeout=180)
        image_response.raise_for_status()
        image_data = image_response.json()
        images = image_data.get('images')
        print(f"Received response from Image API: keys={list(image_data.keys())}")

        if images and isinstance(images, list) and len(images) > 0:
            generated_image_b64 = images[0]
            print("Image generated successfully using refined prompt.")
        else:
            image_gen_error_message = "Image API did not return valid image data."
            print(f"Invalid image data received: {images}")
        # === END Step 2 ===

    # Error Handling (Combined and refined)
    except ValueError as e: print(f"ValueError caught in generate_image: {e}"); image_gen_error_message = str(e)
    except ConnectionError as e: error_msg = f"Error: Could not connect to database. {e}"; image_gen_error_message = error_msg; print(error_msg)
    except requests.exceptions.ConnectionError as e:
        failed_url = e.request.url if e.request else "Unknown API"
        service_name = "Ollama" if ollama_endpoint and ollama_endpoint in failed_url else ("Image Generation (A1111)" if image_api_url and image_api_url in failed_url else "required AI service")
        error_msg = f"Error: Could not connect to {service_name} at {failed_url}."
        image_gen_error_message = error_msg; print(error_msg)
    except requests.exceptions.Timeout as e:
        failed_url = e.request.url if e.request else "Unknown API"
        error_msg = f"Error: The request timed out when contacting {failed_url}."
        image_gen_error_message = error_msg; print(error_msg)
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else 'N/A'
        response_text = e.response.text[:200] if e.response is not None else str(e)
        failed_url = e.request.url if e.request else "Unknown API"
        error_msg = f"API Error ({status_code}) from {failed_url}: {response_text}"
        image_gen_error_message = error_msg; print(f"Error during API call to {failed_url}: {image_gen_error_message}")
    except Exception as e:
        image_gen_error_message = f"An unexpected error occurred generating image: {e}"
        print(f"!! UNEXPECTED ERROR in generate_image: {e}")

    # --- Render the template directly ---
    print(f"Rendering template after image gen attempt with conversation_id: {conversation_id_str}")
    # Pass back ALL context needed
    return render_template('dashboard.html',
                           user=current_user,
                           all_conversations=all_conversations,
                           active_conversation_id=conversation_id_str,
                           chat_history=chat_history,
                           ollama_prompt="", ollama_error=None, last_topic=last_topic,
                           # Image Results/State
                           generated_image_base64=generated_image_b64,
                           image_error=image_gen_error_message,
                           last_image_prompt=user_input_prompt,
                           last_refined_prompt=refined_prompt_for_image,
                           # Audio State (Preserve)
                           supported_languages=SUPPORTED_LANGUAGES,
                           available_speakers=available_speakers,
                           generated_audio_base64=generated_audio_base64, # Pass back if exists
                           audio_error=None, # Clear audio error on image action
                           last_audio_text=last_audio_text,
                           last_language_code=last_language_code,
                           last_speaker_id=last_speaker_id
                           )


# --- Audio Generation Route (From old file, with corrections) ---
@views.route('/generate-audio', methods=['POST'])
@login_required
def generate_audio():
    print("\n--- Handling POST to /generate-audio ---")
    # --- Get form data ---
    user_id_obj = ObjectId(current_user.id)
    text_to_speak = request.form.get('audio_text')
    language_code = request.form.get('language_code', 'en')
    speaker_id_from_form = request.form.get('speaker_id')
    conversation_id_str = request.form.get('conversation_id')
    # Preserve state from other panels
    last_topic = request.form.get('topic', '')
    last_image_prompt = request.form.get('image_prompt', '')
    last_refined_prompt = request.form.get('last_refined_prompt', '')
    generated_image_b64 = request.form.get('generated_image_base64') # Preserve generated image

    print(f"Received form data - user: {user_id_obj}, convo: {conversation_id_str}, text: '{text_to_speak[:50]}...', lang: {language_code}, speaker_selected: {speaker_id_from_form}")

    # --- Variables for template context ---
    generated_audio_b64 = None
    audio_gen_error_message = None
    all_conversations = []
    chat_history = []
    available_speakers = []
    speaker_id_to_use = None # The actual speaker ID we will use
    xtts_api_url_base = None

    try:
        # Corrected DB Check
        if mongo.db is None:
             raise ConnectionError("Database connection not available.")

        # --- Get Config & Fetch Speakers ---
        xtts_api_url_base = get_config_or_raise('XTTS_API_URL')
        available_speakers = get_available_speakers(xtts_api_url_base)
        print(f"Fetched available speakers: {available_speakers}")
        # --- End Config & Fetch ---

        # --- Reload context for rendering template ---
        all_conversations = list(mongo.db.conversations.find({"user_id": user_id_obj}).sort("last_updated", -1))
        if conversation_id_str and ObjectId.is_valid(conversation_id_str):
            conversation = mongo.db.conversations.find_one({"_id": ObjectId(conversation_id_str), "user_id": user_id_obj})
            if conversation: chat_history = conversation.get("messages", [])
            else: conversation_id_str = None; chat_history = []
        # --- End Reloading ---

        # --- Input Validation ---
        if not text_to_speak: raise ValueError("Text for audio generation cannot be empty.")
        if language_code not in SUPPORTED_LANGUAGES: raise ValueError(f"Invalid language code '{language_code}'.")

        # Validate Speaker ID
        if not available_speakers:
             error_msg = "No speaker voices found or loaded from the audio service. Check `./xtts/actors` and TTS service logs."
             raise ValueError(error_msg) # Stop processing

        if speaker_id_from_form and speaker_id_from_form in available_speakers:
             speaker_id_to_use = speaker_id_from_form
             print(f"Using selected speaker: {speaker_id_to_use}")
        else:
             speaker_id_to_use = available_speakers[0] # Default to first
             print(f"WARN: Invalid or missing speaker '{speaker_id_from_form}'. Defaulting to: {speaker_id_to_use}")
             # flash(f"Selected speaker was invalid, using default '{speaker_id_to_use}'.", category='info')

        # === Call the XTTS API ===
        print(f"--- Calling XTTS API ---")
        # !!! VERIFY Endpoint & Payload with daswer123/xtts-api-server docs !!!
        xtts_api_endpoint = f"{xtts_api_url_base}/tts_to_audio" # Verify this path
        payload = {
            "text": text_to_speak,
            "language": language_code,
            "speaker_wav": speaker_id_to_use, # Use the validated ID. API might need .wav extension, verify!
            "options": {} # Add options if needed
        }
        headers = {'Content-Type': 'application/json', 'Accept': 'audio/wav'}
        print(f"*** CALLING XTTS *** -> URL: {xtts_api_endpoint} | Lang: {language_code} | Speaker: {speaker_id_to_use}")
        tts_response = requests.post(xtts_api_endpoint, json=payload, headers=headers, timeout=180)
        print(f"XTTS API Response Status Code: {tts_response.status_code}")

        if not tts_response.ok:
             error_detail = f"Status: {tts_response.status_code}"
             try: error_data = tts_response.json(); detail = error_data.get('detail'); msg = error_data.get('message'); error_detail += f" | Detail: {detail}" if detail else (f" | Message: {msg}" if msg else f" | Response: {str(error_data)[:200]}")
             except json.JSONDecodeError: error_detail += f" | Response: {tts_response.text[:500]}"
             print(f"ERROR from XTTS API: {error_detail}")
             tts_response.raise_for_status() # Raise exception to be caught below

        # Process Successful Response
        content_type = tts_response.headers.get('Content-Type', '').lower()
        print(f"Received response from XTTS API. Content-Type: {content_type}")
        if 'audio/wav' in content_type:
            audio_bytes = tts_response.content
            if not audio_bytes: audio_gen_error_message = "XTTS API returned empty audio content."; print(audio_gen_error_message)
            else: generated_audio_b64 = base64.b64encode(audio_bytes).decode('utf-8'); print(f"Audio generated successfully ({len(audio_bytes)} bytes).")
        else:
            error_detail = tts_response.text[:500]
            audio_gen_error_message = f"XTTS API returned unexpected content type '{content_type}'. Response: {error_detail}"; print(audio_gen_error_message)
        # === END XTTS API Call ===

    # --- Error Handling (Combined and refined) ---
    except ValueError as e: print(f"ValueError caught in generate_audio: {e}"); audio_gen_error_message = str(e)
    except ConnectionError as e: error_msg = f"Error: Could not connect to database. {e}"; audio_gen_error_message = error_msg; print(error_msg)
    except requests.exceptions.ConnectionError as e: error_msg = f"Error: Could not connect to Text-to-Speech service at {xtts_api_url_base}."; audio_gen_error_message = error_msg; print(error_msg)
    except requests.exceptions.Timeout as e: error_msg = f"Error: The request timed out when contacting {xtts_api_endpoint}."; audio_gen_error_message = error_msg; print(error_msg)
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else 'N/A'
        response_text = e.response.text[:500] if e.response is not None else str(e)
        error_msg = f"TTS API Error ({status_code}) from {xtts_api_endpoint}: {response_text}"
        # Use already parsed error detail if available
        if audio_gen_error_message is None: audio_gen_error_message = error_msg
        print(f"Error during TTS API call: {audio_gen_error_message}")
    except Exception as e:
        audio_gen_error_message = f"An unexpected error occurred generating audio: {e}"
        print(f"!! UNEXPECTED ERROR in generate_audio: {e}")

    # --- Always Render Template ---
    print(f"Rendering template after audio gen attempt with conversation_id: {conversation_id_str}")
    return render_template('dashboard.html',
                           user=current_user,
                           all_conversations=all_conversations,
                           active_conversation_id=conversation_id_str,
                           chat_history=chat_history,
                           ollama_prompt="", ollama_error=None, last_topic=last_topic,
                           # Image State (Preserve)
                           generated_image_base64=generated_image_b64,
                           image_error=None, # Clear image error on audio action
                           last_image_prompt=last_image_prompt,
                           last_refined_prompt=last_refined_prompt,
                           # Audio Results/State
                           supported_languages=SUPPORTED_LANGUAGES,
                           available_speakers=available_speakers,
                           generated_audio_base64=generated_audio_b64,
                           audio_error=audio_gen_error_message,
                           last_audio_text=text_to_speak,
                           last_language_code=language_code,
                           last_speaker_id=speaker_id_to_use
                           )
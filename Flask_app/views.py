# --- flask_app/views.py ---

import os
import requests
import json
from flask import (Blueprint, render_template, request, flash,
                   redirect, url_for, current_app, session, jsonify)
from flask_login import login_required, current_user
from bson.objectid import ObjectId
from datetime import datetime
# Assuming your mongo instance is initialized in __init__.py or similar
# and imported like this:
from . import mongo
# If mongo is initialized differently (e.g., directly here), adjust the import/access accordingly.

views = Blueprint('views', __name__)

# --- Constants ---
MAX_HISTORY_MESSAGES = 10 # Number of past messages to send to Ollama for context
CONVERSATION_TITLE_LENGTH = 40 # Max length for auto-generated titles

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

# --- Helper Function to get Config with Error Handling ---
def get_config_or_raise(config_key):
    """Gets a config value from Environment variables or raises ValueError if missing."""
    value = os.environ.get(config_key)
    if not value:
        error_msg = f"Configuration Error: Required environment variable '{config_key}' is missing or empty."
        print(f"!!! {error_msg} !!!") # Log error clearly
        raise ValueError(error_msg)
    print(f"DEBUG: Read ENV VAR {config_key} = {value}") # Print value read
    return value

# --- Home Route ---
@views.route('/')
def home():
    return render_template("home.html", user=current_user)

# --- Dashboard Route (Handles GET requests) ---
@views.route('/dashboard')
@login_required
def dashboard():
    user_id_obj = ObjectId(current_user.id)
    print(f"\n--- Loading Dashboard for user: {user_id_obj} ---")

    all_conversations = []
    active_conversation_id_str = request.args.get('conversation_id')
    active_conversation = None
    active_chat_history = []

    try:
        # Fetch sidebar conversations
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
    except Exception as e:
        print(f"Error loading dashboard data: {e}")
        # Use current_app.logger in production for better logging
        # current_app.logger.error(f"Error loading dashboard data: {e}", exc_info=True)
        flash("Error loading dashboard data.", category='error')
        all_conversations = []
        active_conversation_id_str = None
        active_chat_history = []

    print(f"Rendering dashboard with active_conversation_id: {active_conversation_id_str}, history length: {len(active_chat_history)}")
    return render_template("dashboard.html",
                           user=current_user,
                           all_conversations=all_conversations,
                           active_conversation_id=active_conversation_id_str,
                           chat_history=active_chat_history,
                           generated_image_base64=None, ollama_prompt="", ollama_error=None,
                           last_topic="", image_error=None, last_image_prompt="",
                           last_refined_prompt="")

# --- Ollama Text Generation Route ---
@views.route('/generate_text_prompt', methods=['POST'])
@login_required
def generate_text_prompt():
    print("\n--- Handling POST to /generate_text_prompt ---")
    user_id_obj = ObjectId(current_user.id)
    conversation_id_str = request.form.get('conversation_id')
    user_input_topic = request.form.get('topic')
    print(f"Received form data - user: {user_id_obj}, conversation_id: {conversation_id_str}, topic: {user_input_topic}")

    if not user_input_topic:
        flash("Please enter a topic or message.", category='error')
        redirect_url = url_for('views.dashboard', conversation_id=conversation_id_str) if conversation_id_str else url_for('views.dashboard')
        return redirect(redirect_url)

    conversation_object_id = None
    conversation_history = []
    ollama_endpoint = None # Initialize for use in error message

    try:
        # --- Get Config Variables FIRST ---
        ollama_endpoint = get_config_or_raise('OLLAMA_ENDPOINT') # e.g., http://ollama:11434
        ollama_model = get_config_or_raise('OLLAMA_MODEL')       # e.g., llama3
        # --- End Config Check ---

        # 1. Find or Create Conversation (Same logic as before)
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
            conversation_history = []
        if not conversation_object_id:
            title = user_input_topic[:CONVERSATION_TITLE_LENGTH] + ('...' if len(user_input_topic) > CONVERSATION_TITLE_LENGTH else '')
            new_convo_doc = {"user_id": user_id_obj, "title": title, "created_at": datetime.utcnow(), "last_updated": datetime.utcnow(), "messages": []}
            insert_result = mongo.db.conversations.insert_one(new_convo_doc)
            conversation_object_id = insert_result.inserted_id
            conversation_id_str = str(conversation_object_id)
            print(f"Created new conversation with ID: {conversation_id_str}, Title: '{title}'")

        # 2. Prepare Prompt for Ollama
        user_message_content = user_input_topic
        messages_for_ollama = [{"role": "system", "content": MARKETING_SYSTEM_PROMPT.strip()}]
        if conversation_history:
            recent_history = conversation_history[-MAX_HISTORY_MESSAGES:]
            for msg in recent_history:
                 if msg.get('role') and msg.get('content'):
                    messages_for_ollama.append({"role": msg['role'], "content": msg['content']})
        messages_for_ollama.append({"role": "user", "content": user_message_content})
        print(f"--- Messages prepared for Ollama API (count: {len(messages_for_ollama)}) ---")

        # 3. Call Ollama API
        payload = {"model": ollama_model, "messages": messages_for_ollama, "stream": False}
        ollama_api_url = f"{ollama_endpoint}/api/chat" # Construct the *full* URL here

        print(f"*** CALLING OLLAMA (TEXT) *** -> URL: {ollama_api_url}") # <<< Enhanced Log

        response = requests.post(ollama_api_url, json=payload, timeout=90)
        response.raise_for_status()
        data = response.json()
        latest_ai_response = data.get('message', {}).get('content', '').strip()

        if not latest_ai_response: print("Ollama returned empty response content.")
        else: print(f"Ollama response received: {latest_ai_response[:100]}...")

        # 4. Save interaction to MongoDB (Same logic as before)
        user_msg_doc = {"role": "user", "content": user_message_content, "timestamp": datetime.utcnow()}
        messages_to_save = [user_msg_doc]
        if latest_ai_response:
            ai_msg_doc = {"role": "assistant", "content": latest_ai_response, "timestamp": datetime.utcnow()}
            messages_to_save.append(ai_msg_doc)
        print(f"Saving {len(messages_to_save)} messages to conversation ID: {conversation_object_id}")
        mongo.db.conversations.update_one({"_id": conversation_object_id},{"$push": {"messages": {"$each": messages_to_save}}, "$set": {"last_updated": datetime.utcnow()}})

    # --- Error Handling (Same logic as before) ---
    except ValueError as e: print(f"Error in generate_text_prompt: {e}"); flash(str(e), category='error')
    except requests.exceptions.ConnectionError as e: error_msg = f"..."; flash(error_msg, category='error'); print(error_msg) # Keep full error messages
    except requests.exceptions.Timeout: error_msg = f"..."; flash(error_msg, category='error'); print(error_msg) # Keep full error messages
    except requests.exceptions.RequestException as e: error_detail = f"..."; error_msg = f"..."; flash(error_msg, category='error'); print(f"Request Error: {error_msg} (URL: {ollama_api_url})") # Keep full error messages
    except Exception as e: error_msg = f"..."; flash(error_msg, category='error'); print(f"!! UNEXPECTED ERROR: {e}") # Keep full error messages

    # 5. Redirect back to the dashboard
    return redirect(url_for('views.dashboard', conversation_id=conversation_id_str))


# --- Image Generation Route ---
@views.route('/generate-image', methods=['POST'])
@login_required
def generate_image():
    print("\n--- Handling POST to /generate-image ---")
    user_id_obj = ObjectId(current_user.id)

    # --- Get initial form data ---
    user_input_prompt = request.form.get('image_prompt')
    conversation_id_str = request.form.get('conversation_id')
    last_topic = request.form.get('topic', '')

    print(f"Received form data - user: {user_id_obj}, conversation_id: {conversation_id_str}, initial_image_prompt: {user_input_prompt}")

    # --- Variables needed for rendering template ---
    generated_image_b64 = None
    image_gen_error_message = None
    refined_prompt_for_image = ""
    all_conversations = []
    chat_history = []
    ollama_endpoint = None # Initialize for use in error messages
    image_api_url = None # Initialize for use in error messages

    try:
        # --- Get Config Variables FIRST ---
        ollama_endpoint = get_config_or_raise('OLLAMA_ENDPOINT')
        ollama_model = get_config_or_raise('OLLAMA_MODEL')
        image_api_url = get_config_or_raise('IMAGE_API_URL') # This is the base URL for A1111
        # --- End Config Check ---

        # --- Reload conversation data needed for rendering the page ---
        all_conversations = list(mongo.db.conversations.find({"user_id": user_id_obj}).sort("last_updated", -1))
        if conversation_id_str and ObjectId.is_valid(conversation_id_str):
            conversation = mongo.db.conversations.find_one({"_id": ObjectId(conversation_id_str), "user_id": user_id_obj})
            if conversation: chat_history = conversation.get("messages", [])
            else: conversation_id_str = None; chat_history = []
        # --- End Reloading ---

        # --- Check if initial prompt is empty ---
        if not user_input_prompt:
            image_gen_error_message = "Image prompt cannot be empty."
            raise ValueError(image_gen_error_message)

        # === Step 1: Refine prompt using Ollama ===
        print(f"--- Step 1: Refining prompt using Ollama ---")
        refinement_payload = {"model": ollama_model,"messages": [{"role": "system", "content": IMAGE_PROMPT_REFINEMENT_SYSTEM_PROMPT.strip()}, {"role": "user", "content": user_input_prompt}],"stream": False }
        ollama_api_url = f"{ollama_endpoint}/api/chat" # Construct the *full* URL here

        print(f"*** CALLING OLLAMA (REFINE) *** -> URL: {ollama_api_url}") # <<< Enhanced Log

        refine_response = requests.post(ollama_api_url, json=refinement_payload, timeout=60)
        refine_response.raise_for_status()
        refine_data = refine_response.json()
        refined_prompt_for_image = refine_data.get('message', {}).get('content', '').strip()

        if not refined_prompt_for_image:
            print("Ollama refinement returned empty response, falling back to original prompt.")
            refined_prompt_for_image = user_input_prompt
        else:
             print(f"Refined prompt received: {refined_prompt_for_image}")

        # === Step 2: Call the Image Generation API (AUTOMATIC1111) ===
        print(f"--- Step 2: Generating image using AUTOMATIC1111 ---")
        # Construct the *full* URL using the BASE URL from config + specific endpoint path
        image_api_endpoint = f"{image_api_url}/sdapi/v1/txt2img" # The A1111 endpoint
        image_payload = {
            "prompt": refined_prompt_for_image, # Use the refined prompt
            "steps": 25, "width": 512, "height": 512, "sampler_index": "Euler a",
            "negative_prompt": "ugly, deformed, blurry, low quality, text, words, signature, watermark, username, person, people"
        }

        print(f"*** CALLING A1111 (IMAGE) *** -> URL: {image_api_endpoint}") # <<< Enhanced Log
        print(f"Image Payload Prompt: {image_payload['prompt'][:100]}...") # Log part of prompt being sent

        image_response = requests.post(image_api_endpoint, json=image_payload, timeout=180) # Longer timeout
        image_response.raise_for_status()
        image_data = image_response.json()
        images = image_data.get('images')
        print(f"Received response from Image API: keys={list(image_data.keys())}")

        if images and isinstance(images, list) and len(images) > 0:
            generated_image_b64 = images[0]
            print("Image generated successfully using refined prompt.")
        else:
            image_gen_error_message = "Image API did not return valid image data using the refined prompt."
            print(f"Invalid image data received: {images}")
        # === END Step 2 ===

    # --- Error Handling (Same logic as before) ---
    except ValueError as e: print(f"ValueError caught in generate_image: {e}"); image_gen_error_message = str(e) # Keep full error messages
    except requests.exceptions.ConnectionError as e: failed_url = "..."; service_name = "..."; image_gen_error_message = f"..."; print(image_gen_error_message) # Keep full error messages
    except requests.exceptions.Timeout as e: failed_url = "..."; image_gen_error_message = f"..."; print(image_gen_error_message) # Keep full error messages
    except requests.exceptions.RequestException as e: error_detail = "..."; status_code = "..."; response_text = "..."; failed_url = "..."; image_gen_error_message = f"..."; print(f"Error during API call to {failed_url}: {image_gen_error_message}") # Keep full error messages
    except Exception as e: image_gen_error_message = f"..."; print(f"!! UNEXPECTED ERROR: {e}") # Keep full error messages

    # --- Render the template directly ---
    # Always render the template, passing errors or results
    print(f"Rendering template after image gen attempt with conversation_id: {conversation_id_str}")
    return render_template('dashboard.html',
                           user=current_user,
                           all_conversations=all_conversations,
                           active_conversation_id=conversation_id_str,
                           chat_history=chat_history,
                           ollama_prompt="", ollama_error=None, last_topic=last_topic,
                           generated_image_base64=generated_image_b64,
                           image_error=image_gen_error_message,
                           last_image_prompt=user_input_prompt,
                           last_refined_prompt=refined_prompt_for_image
                           )

# --- Add other routes (like delete conversation) if needed ---
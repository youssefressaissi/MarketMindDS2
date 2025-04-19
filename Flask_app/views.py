import requests
import json
from flask import (Blueprint, render_template, request, flash,
                   redirect, url_for, current_app, session) # Keep session import for now if auth uses it
from flask_login import login_required, current_user
from bson.objectid import ObjectId
from datetime import datetime
from . import mongo

views = Blueprint('views', __name__)

MAX_HISTORY_MESSAGES = 10
CONVERSATION_TITLE_LENGTH = 40

# --- Home Route (No change) ---
@views.route('/')
def home():
    return render_template("home.html", user=current_user)

# --- MODIFIED Dashboard Route (Handles GET requests) ---
@views.route('/dashboard')
@login_required
def dashboard():
    user_id_obj = ObjectId(current_user.id)
    print(f"\n--- Loading Dashboard for user: {user_id_obj} ---")

    # 1. Fetch all conversations for the sidebar
    all_conversations_cursor = mongo.db.conversations.find({
        "user_id": user_id_obj
    }).sort("last_updated", -1)
    all_conversations = list(all_conversations_cursor)
    print(f"Found {len(all_conversations)} conversations for sidebar.")

    # 2. Determine the active conversation ONLY if ID is provided
    active_conversation_id_str = request.args.get('conversation_id') # Get ID from URL
    active_conversation = None
    active_chat_history = []

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
            # Don't flash here, just render empty state below
            active_conversation_id_str = None # Reset ID

    # <<<< REMOVED the 'elif all_conversations:' block >>>>
    # Now, if no valid ID is given, active_conversation_id_str remains None
    # and active_chat_history remains empty.

    print(f"Rendering dashboard with active_conversation_id: {active_conversation_id_str}")
    print(f"Rendering dashboard with history length: {len(active_chat_history)}")

    # Check session for a flashed image from a previous redirect (might still exist)
    # This is less reliable now, but keep it for potential edge cases or future use
    last_image = session.pop('last_generated_image', None)

    return render_template("dashboard.html",
                           user=current_user,
                           all_conversations=all_conversations,
                           active_conversation_id=active_conversation_id_str,
                           chat_history=active_chat_history,
                           # Pass flashed image if present
                           generated_image_base64=last_image,
                           # Initialize other fields
                           ollama_prompt="",
                           ollama_error=None,
                           last_topic="",
                           image_error=None,
                           last_image_prompt=""
                           )

# --- Ollama Text Generation Route (Still uses redirect) ---
@views.route('/generate_text_prompt', methods=['POST'])
@login_required
def generate_text_prompt():
    print("\n--- Handling POST to /generate_text_prompt ---")
    conversation_id_str = request.form.get('conversation_id')
    user_input_topic = request.form.get('topic')
    print(f"Received form data - conversation_id: {conversation_id_str}, topic: {user_input_topic}")

    if not user_input_topic:
        flash("Please enter a topic or message.", category='error')
        redirect_url = url_for('views.dashboard', conversation_id=conversation_id_str) if conversation_id_str else url_for('views.dashboard')
        return redirect(redirect_url)

    try:
        conversation_object_id = None
        conversation = None
        conversation_history = []
        is_new_conversation = False

        # 1. Find or Create Conversation (Logic remains the same)
        if conversation_id_str and ObjectId.is_valid(conversation_id_str):
            # ... (find existing logic) ...
            conversation_object_id = ObjectId(conversation_id_str)
            conversation = mongo.db.conversations.find_one({"_id": conversation_object_id, "user_id": ObjectId(current_user.id)})
            if conversation: conversation_history = conversation.get("messages", [])
            else: conversation_id_str = None; conversation_object_id = None
        # ... (end find existing logic) ...

        if not conversation_object_id:
            # ... (create new conversation logic including title) ...
             is_new_conversation = True
             title = user_input_topic[:CONVERSATION_TITLE_LENGTH] + ('...' if len(user_input_topic) > CONVERSATION_TITLE_LENGTH else '')
             new_convo_doc = { "user_id": ObjectId(current_user.id), "title": title, "created_at": datetime.utcnow(), "last_updated": datetime.utcnow(), "messages": [] }
             insert_result = mongo.db.conversations.insert_one(new_convo_doc)
             conversation_object_id = insert_result.inserted_id
             conversation_id_str = str(conversation_object_id)
             conversation_history = []
             print(f"Created new conversation with ID: {conversation_id_str}, Title: '{title}'")
        # ... (end create new logic) ...

        # 2. Prepare Prompt for Ollama (Logic remains the same)
        # ... (format history, combine prompt) ...
        user_message_content = user_input_topic
        formatted_history_for_prompt = []
        if conversation_history:
             recent_history = conversation_history[-MAX_HISTORY_MESSAGES:]
             for msg in recent_history: formatted_history_for_prompt.append(f"{msg['role'].capitalize()}: {msg['content']}")
        full_prompt_for_ollama = "\n".join(formatted_history_for_prompt) + f"\nUser: {user_message_content}"
        print(f"--- Prompt sent to Ollama (length {len(full_prompt_for_ollama)}): ---\n{full_prompt_for_ollama[:300]}...\n---------------------------------")

        ollama_endpoint = current_app.config['OLLAMA_ENDPOINT']
        ollama_model = current_app.config['OLLAMA_MODEL']
        payload = {"model": ollama_model, "prompt": full_prompt_for_ollama.strip(), "stream": False}

        # 3. Call Ollama API (Logic remains the same)
        # ... (requests.post call) ...
        print(f"Calling Ollama API: {ollama_endpoint}")
        response = requests.post(ollama_endpoint, json=payload, timeout=90)
        response.raise_for_status()
        data = response.json()
        latest_ai_response = data.get('response', '').strip()
        print(f"Ollama response received: {latest_ai_response[:100]}...")

        # 4. Save interaction to MongoDB (Logic remains the same, including check)
        if not latest_ai_response:
             flash("Ollama returned an empty response.", category='warning')
             user_msg_doc = {"role": "user", "content": user_message_content, "timestamp": datetime.utcnow()}
             mongo.db.conversations.update_one({"_id": conversation_object_id}, {"$push": {"messages": user_msg_doc}, "$set": {"last_updated": datetime.utcnow()}})
        else:
            user_msg_doc = {"role": "user", "content": user_message_content, "timestamp": datetime.utcnow()}
            ai_msg_doc = {"role": "assistant", "content": latest_ai_response, "timestamp": datetime.utcnow()}
            print(f"Saving messages to conversation ID: {conversation_object_id}")
            update_result = mongo.db.conversations.update_one(
                {"_id": conversation_object_id},
                {"$push": {"messages": {"$each": [user_msg_doc, ai_msg_doc]}}, "$set": {"last_updated": datetime.utcnow()}}
            )
            if update_result.modified_count != 1: print(f"ERROR: Failed to save messages to DB.")

    # --- Error Handling (remains the same) ---
    except requests.exceptions.RequestException as e:
        flash(f"Error contacting Ollama: {e}", category='error')
        print(f"Error contacting Ollama: {e}")
    # ... other except blocks ...
    except Exception as e:
         flash(f"An unexpected error occurred: {e}", category='error')
         current_app.logger.error(f"Unexpected error in generate_text_prompt: {e}", exc_info=True)
         print(f"!! UNEXPECTED ERROR: {e}")

    # 5. Redirect back to the dashboard, displaying the *current* conversation
    return redirect(url_for('views.dashboard', conversation_id=conversation_id_str))


# --- MODIFIED Image Generation Route (Handles POST, renders directly) ---
@views.route('/generate-image', methods=['POST'])
@login_required
def generate_image():
    print("\n--- Handling POST to /generate-image ---")
    # Variables needed for rendering the template at the end
    generated_image_b64 = None
    image_gen_error_message = None # Use a different variable name to avoid clashes
    user_input_prompt = request.form.get('image_prompt')
    conversation_id_str = request.form.get('conversation_id')
    last_topic = request.form.get('topic', '') # Preserve topic state

    print(f"Received form data - conversation_id: {conversation_id_str}, image_prompt: {user_input_prompt}")

    # Reload conversation history needed for rendering the sidebar and chat context
    all_conversations = list(mongo.db.conversations.find({"user_id": ObjectId(current_user.id)}).sort("last_updated", -1))
    chat_history = []
    if conversation_id_str and ObjectId.is_valid(conversation_id_str):
        conversation = mongo.db.conversations.find_one({
            "_id": ObjectId(conversation_id_str),
            "user_id": ObjectId(current_user.id)
        })
        if conversation:
            chat_history = conversation.get("messages", [])
    # <<< Ensure chat_history is loaded for the active conversation! >>>

    if not user_input_prompt:
        image_gen_error_message = "Image prompt cannot be empty."
        # No flash needed here as we render directly
    else:
        # Call the Image API (logic remains the same)
        image_api_url = current_app.config['IMAGE_API_URL']
        api_endpoint = f"{image_api_url}/sdapi/v1/txt2img"
        payload = { "prompt": user_input_prompt, "steps": 25, "width": 512, "height": 512, "sampler_index": "Euler a"}

        try:
            print(f"Sending request to Image API: {api_endpoint}")
            response = requests.post(api_endpoint, json=payload, timeout=180)
            response.raise_for_status()
            data = response.json()
            images = data.get('images')
            print(f"Received response from Image API: keys={list(data.keys())}") # Debug

            if images and isinstance(images, list) and len(images) > 0:
                generated_image_b64 = images[0] # Store the result for rendering
                print("Image generated successfully.")
            else:
                image_gen_error_message = "Image API did not return valid image data."
                print(f"Invalid image data received: {images}")

        # --- Error Handling (remains the same, but use image_gen_error_message) ---
        except requests.exceptions.ConnectionError:
            image_gen_error_message = f"Error: Could not connect to Image Generation service at {image_api_url}."
        except requests.exceptions.Timeout:
             image_gen_error_message = "Error: Image generation request timed out."
        except requests.exceptions.RequestException as e:
            error_detail = str(e)
            # --- CORRECTED SYNTAX HERE ---
            if e.response is not None:
                try:
                    error_detail += f" | Response: {e.response.text}"
                except Exception:
                    pass # Ignore if response text cannot be read/decoded
            # --- END CORRECTION ---
            image_gen_error_message = f"Error contacting Image API: {error_detail}"
    # <<<< RENDER TEMPLATE DIRECTLY INSTEAD OF REDIRECTING >>>>
    print(f"Rendering template after image gen with conversation_id: {conversation_id_str}")
    return render_template('dashboard.html',
                           user=current_user,
                           all_conversations=all_conversations, # Need to pass this for sidebar
                           active_conversation_id=conversation_id_str, # Preserve active convo ID
                           chat_history=chat_history, # Preserve active chat history
                           ollama_prompt="", # Clear Ollama prompt area
                           ollama_error=None,
                           last_topic=last_topic, # Preserve last topic
                           # Pass back image results
                           generated_image_base64=generated_image_b64, # The generated image (or None)
                           image_error=image_gen_error_message, # The specific image error (or None)
                           last_image_prompt=user_input_prompt # Pass submitted prompt back
                           )
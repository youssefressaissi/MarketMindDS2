# flask_app/__init__.py

import os
import time
from flask import Flask
from flask_pymongo import PyMongo
from flask_login import LoginManager
from bson.objectid import ObjectId
from bson.errors import InvalidId

# Initialize extensions globally
mongo = PyMongo()
login_manager = LoginManager()
login_manager.login_view = 'auth.login' # Route to redirect to if login is required

DEFAULT_OLLAMA_MODEL = "llama3:latest" # Use a common default like llama3

def create_app():
    app = Flask(__name__)
    print("--- Creating Flask App ---")

    # --- Configuration from Environment Variables ---
    # Load directly into app.config for simpler access later if needed,
    # but primary access in views will be via os.environ using get_config_or_raise.
    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'fallback-insecure-secret-key-change-me')

    # Set the MONGO_URI config variable, which Flask-PyMongo expects
    app.config['MONGO_URI'] = os.environ.get('MONGO_URL', 'mongodb://localhost:27017/marketmind_dev') # Default for local dev if not set

    # Load other service URLs
    app.config['IMAGE_API_URL'] = os.environ.get('IMAGE_API_URL', 'http://localhost:7860') # A1111 base URL
    app.config['OLLAMA_ENDPOINT'] = os.environ.get('OLLAMA_ENDPOINT', 'http://localhost:11434') # Ollama BASE URL
    app.config['OLLAMA_MODEL'] = os.environ.get('OLLAMA_MODEL', DEFAULT_OLLAMA_MODEL)
    app.config['XTTS_API_URL'] = os.environ.get('XTTS_API_URL', 'http://localhost:8020') # XTTS BASE URL
    
    # --- >>> ADD VIDEO_API_URL CONFIGURATION <<< ---
    app.config['VIDEO_API_URL'] = os.environ.get('VIDEO_API_URL', 'http://localhost:8188') # ComfyUI BASE URL (Default for local dev)
    # --- >>> END OF ADDED LINE <<< ---

    # Print loaded config for debugging during startup
    print(f"DEBUG [__init__]: SECRET_KEY loaded: {'Yes' if app.config['SECRET_KEY'] != 'fallback-insecure-secret-key-change-me' else 'No (Using Default)'}")
    print(f"DEBUG [__init__]: MONGO_URI = {app.config['MONGO_URI']}")
    print(f"DEBUG [__init__]: IMAGE_API_URL = {app.config['IMAGE_API_URL']}")
    print(f"DEBUG [__init__]: OLLAMA_ENDPOINT = {app.config['OLLAMA_ENDPOINT']}")
    print(f"DEBUG [__init__]: OLLAMA_MODEL = {app.config['OLLAMA_MODEL']}")
    print(f"DEBUG [__init__]: XTTS_API_URL = {app.config['XTTS_API_URL']}")
    # --- >>> ADD VIDEO_API_URL DEBUG PRINT <<< ---
    print(f"DEBUG [__init__]: VIDEO_API_URL = {app.config['VIDEO_API_URL']}")
    # --- >>> END OF ADDED LINE <<< ---
    # --- End Configuration ---

    # Initialize extensions with the app instance
    max_retries = 5
    retry_delay = 2  # seconds

    for attempt in range(max_retries):
        try:
            print(f"Attempting to initialize MongoDB (attempt {attempt + 1}/{max_retries})...")
            mongo.init_app(app)
            # Test the connection using a simple command
            mongo.db.command('ping')
            print("MongoDB initialized and connection verified.")
            break # Exit loop on success
        except Exception as e:
            print(f"WARN: MongoDB connection attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                print(f"!!! FATAL: Error initializing MongoDB after {max_retries} attempts. The application might not function correctly without a database.")
                # Decide if you want to raise the exception or allow the app to continue without DB
                # raise ConnectionError(f"Failed to connect to MongoDB after {max_retries} attempts: {e}") from e
            else:
                print(f"Retrying MongoDB initialization in {retry_delay} seconds...")
                time.sleep(retry_delay)

    login_manager.init_app(app)
    print("LoginManager initialized.")

    # --- Register Blueprints ---
    try:
        from .views import views
        from .auth import auth
        app.register_blueprint(views, url_prefix='/')
        app.register_blueprint(auth, url_prefix='/')
        print("Blueprints registered successfully.")
    except ImportError as e:
        print(f"!!! Error importing or registering blueprints: {e}")
        # This is likely a critical error, consider raising it
        raise ImportError(f"Failed to import blueprints: {e}") from e
    # --- End Blueprint Registration ---

    @login_manager.user_loader
    def load_user(user_id):
        """Loads user object from MongoDB based on session user_id."""
        print(f"User Loader: Attempting to load user with id: {user_id}")
        if not user_id: return None
        try:
            # Validate and convert user_id to ObjectId
            user_obj_id = ObjectId(user_id)
            # Ensure mongo.db is available (check after init_app attempts)
            if mongo.db is None:
                 print("User Loader: mongo.db is None, cannot query database.")
                 return None

            user_data = mongo.db.users.find_one({"_id": user_obj_id})
            # print(f"User Loader: User data from DB: {user_data}") # Debug: can be verbose

            if user_data:
                # Dynamically import User model here to avoid circular imports
                # Ensure your User model can be initialized like this
                try:
                    from .models import User # Assuming models.py exists with User class
                    print("User Loader: User data found, creating User object.")
                    return User(user_data=user_data)
                except ImportError:
                     print("!!! User Loader: Failed to import User model from .models")
                     return None
            else:
                print("User Loader: User ID not found in database.")
                return None
        except InvalidId:
            print(f"User Loader: Invalid ObjectId format in session: {user_id}")
            return None
        except Exception as e:
            print(f"!!! User Loader: Error loading user {user_id}: {e}")
            # Log the full traceback in production environments for better debugging
            # import traceback
            # print(traceback.format_exc())
            return None

    print("Flask app creation completed.")
    return app
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
    
    # ***** CORRECTION IS HERE *****
    # Set the MONGO_URI config variable, which Flask-PyMongo expects
    app.config['MONGO_URI'] = os.environ.get('MONGO_URL', 'mongodb://localhost:27017/marketmind_dev') 
    # ***** END CORRECTION *****
    
    app.config['IMAGE_API_URL'] = os.environ.get('IMAGE_API_URL', 'http://localhost:7860') # A1111 base URL
    app.config['OLLAMA_ENDPOINT'] = os.environ.get('OLLAMA_ENDPOINT', 'http://localhost:11434') # Ollama BASE URL
    app.config['OLLAMA_MODEL'] = os.environ.get('OLLAMA_MODEL', DEFAULT_OLLAMA_MODEL)
    app.config['XTTS_API_URL'] = os.environ.get('XTTS_API_URL', 'http://localhost:8020') # XTTS BASE URL

    # Print loaded config for debugging during startup
    print(f"DEBUG [__init__]: SECRET_KEY loaded: {'Yes' if app.config['SECRET_KEY'] != 'fallback-insecure-secret-key-change-me' else 'No (Using Default)'}")
    
    # ***** CORRECTION IS HERE *****
    # Update the debug print to show the key Flask-PyMongo uses
    print(f"DEBUG [__init__]: MONGO_URI = {app.config['MONGO_URI']}")
    # ***** END CORRECTION *****
    
    print(f"DEBUG [__init__]: IMAGE_API_URL = {app.config['IMAGE_API_URL']}")
    print(f"DEBUG [__init__]: OLLAMA_ENDPOINT = {app.config['OLLAMA_ENDPOINT']}")
    print(f"DEBUG [__init__]: OLLAMA_MODEL = {app.config['OLLAMA_MODEL']}")
    print(f"DEBUG [__init__]: XTTS_API_URL = {app.config['XTTS_API_URL']}")
    # --- End Configuration ---

    # Initialize extensions with the app instance
    max_retries = 5
    retry_delay = 2  # seconds
    
    for attempt in range(max_retries):
        try:
            print(f"Attempting to initialize MongoDB (attempt {attempt + 1}/{max_retries})...")
            mongo.init_app(app)
            # Test the connection
            mongo.db.command('ping')
            print("MongoDB initialized and connection verified.")
            break
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"!!! Error initializing MongoDB after {max_retries} attempts: {e}")
                raise
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
    # --- End Blueprint Registration ---

    @login_manager.user_loader
    def load_user(user_id):
        print(f"User Loader: Attempting to load user with id: {user_id}")
        if not user_id: return None
        try:
            user_obj_id = ObjectId(user_id)
            # Access mongo.db directly (Flask-PyMongo provides this)
            user_data = mongo.db.users.find_one({"_id": user_obj_id})
            # print(f"User Loader: User data from DB: {user_data}") # Can be verbose

            if user_data:
                from .models import User # Assuming models.py exists with User class
                print("User Loader: User data found, creating User object.")
                # Ensure your User class can be initialized with the dictionary from MongoDB
                # Example: def __init__(self, user_data): self.id = str(user_data['_id']); self.email = user_data['email'] ...
                return User(user_data=user_data)
            else:
                print("User Loader: User ID not found in database.")
                return None
        except InvalidId:
            print(f"User Loader: Invalid ObjectId format in session: {user_id}")
            return None
        except Exception as e:
            print(f"!!! User Loader: Error loading user {user_id}: {e}")
            # Log the full traceback in production environments
            # app.logger.error(f"Error in user_loader for {user_id}: {e}", exc_info=True)
            return None

    print("Flask app creation completed.")
    return app
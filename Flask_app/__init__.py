# flask_app/__init__.py

import os
from flask import Flask
from flask_pymongo import PyMongo
from flask_login import LoginManager
from bson.objectid import ObjectId
from bson.errors import InvalidId

# Initialize extensions globally but configure them inside create_app
mongo = PyMongo()
login_manager = LoginManager()
login_manager.login_view = 'auth.login' # Route to redirect to if login is required

# --- REMOVED reading ENV VARS at module level ---
# Define a default model (make sure you pull this model in Ollama)
DEFAULT_OLLAMA_MODEL = "orca-mini:latest"
# --- End Ollama Config ---


def create_app():
    app = Flask(__name__)

    # --- Configuration from Environment Variables ---
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'fallback-insecure-secret-key-change-me')
    mongo_uri = os.environ.get('MONGO_URL', 'mongodb://localhost:27017/MarketMind_dev')
    app.config["MONGO_URI"] = mongo_uri
    app.config['IMAGE_API_URL'] = os.environ.get('IMAGE_API_URL', 'http://localhost:7860')

    # --- MODIFIED: Read Ollama ENV VARS and Store config INSIDE create_app ---
    ollama_api_url = os.environ.get('OLLAMA_API_URL', 'http://localhost:11434') # Read ENV VAR here
    app.config['OLLAMA_ENDPOINT'] = ollama_api_url + '/api/generate'             # Set config key here
    app.config['OLLAMA_MODEL'] = DEFAULT_OLLAMA_MODEL                            # Set config key here
    print(f"DEBUG: Reading OLLAMA_API_URL as: {ollama_api_url}")                  # Added DEBUG print
    print(f"DEBUG: Set app.config['OLLAMA_ENDPOINT'] = {app.config['OLLAMA_ENDPOINT']}") # Added DEBUG print
    # --- End Ollama App Config ---

    # Initialize extensions with the app instance
    mongo.init_app(app)
    login_manager.init_app(app)

    # --- Register Blueprints using relative imports ---
    try:
        from .views import views
        from .auth import auth
        app.register_blueprint(views, url_prefix='/')
        app.register_blueprint(auth, url_prefix='/')
        print("Blueprints registered successfully.")
    except ImportError as e:
        print(f"Error importing or registering blueprints: {e}")

    # --- End Blueprint Registration ---


    @login_manager.user_loader
    def load_user(user_id):
        # ... (keep existing user_loader code) ...
        print(f"Attempting to load user with id: {user_id}")
        if not user_id: return None
        try:
            user_obj_id = ObjectId(user_id)
            user_data = mongo.db.users.find_one({"_id": user_obj_id})
            print(f"User data from DB: {user_data}")

            if user_data:
                from .models import User
                print("User data found, creating User object.")
                return User(user_data)
            else:
                print("User ID not found in database.")
                return None
        except InvalidId:
            print(f"Invalid ObjectId format in session: {user_id}")
            return None
        except Exception as e:
            print(f"Error loading user {user_id}: {e}")
            return None

    print("Flask app created successfully.")
    return app
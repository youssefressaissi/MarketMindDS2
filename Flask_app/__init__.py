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

def create_app():
    app = Flask(__name__)

    # --- Configuration from Environment Variables ---
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'fallback-insecure-secret-key-change-me')
    mongo_uri = os.environ.get('MONGO_URL', 'mongodb://localhost:27017/MarketMind_dev')
    app.config["MONGO_URI"] = mongo_uri
    app.config['IMAGE_API_URL'] = os.environ.get('IMAGE_API_URL', 'http://localhost:7860')
    # --- End Configuration ---

    # Initialize extensions with the app instance
    mongo.init_app(app)
    login_manager.init_app(app)

    # --- Register Blueprints using relative imports ---
    # Assuming views.py, auth.py, and models.py are in the same directory as this __init__.py
    try:
        from .views import views  # '.' means import from current directory
        from .auth import auth    # '.' means import from current directory
        app.register_blueprint(views, url_prefix='/')
        app.register_blueprint(auth, url_prefix='/')
        print("Blueprints registered successfully.") # Add print statement
    except ImportError as e:
        print(f"Error importing or registering blueprints: {e}")
        # Decide how to handle this - maybe raise error?
    # --- End Blueprint Registration ---


    @login_manager.user_loader
    def load_user(user_id):
        """Loads the user object from the database based on the user_id stored in the session."""
        print(f"Attempting to load user with id: {user_id}") # Add print statement
        if not user_id: return None
        try:
            user_obj_id = ObjectId(user_id)
            # Make sure the collection name 'users' is correct
            user_data = mongo.db.users.find_one({"_id": user_obj_id})
            print(f"User data from DB: {user_data}") # Add print statement

            if user_data:
                from .models import User # '.' means import from current directory
                print("User data found, creating User object.") # Add print statement
                return User(user_data)
            else:
                print("User ID not found in database.") # Add print statement
                return None
        except InvalidId:
            print(f"Invalid ObjectId format in session: {user_id}")
            return None
        except Exception as e:
            print(f"Error loading user {user_id}: {e}")
            return None # Safer to return None if any error occurs during loading

    print("Flask app created successfully.") # Add print statement
    return app
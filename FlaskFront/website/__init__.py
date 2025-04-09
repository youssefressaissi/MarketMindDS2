from flask import Flask
from flask_pymongo import PyMongo
from flask_login import LoginManager
from bson.objectid import ObjectId

mongo = PyMongo()
DB_NAME = "MarketMind"  # Changed to match your database name

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'azerty'
    app.config["MONGO_URI"] = "mongodb://localhost:27017/MarketMind"
    
    mongo.init_app(app)

    from .views import views
    from .auth import auth

    app.register_blueprint(views, url_prefix='/')
    app.register_blueprint(auth, url_prefix='/')

    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        try:
             # Convert string ID to ObjectId
            user_data = mongo.db.users.find_one({"_id": ObjectId(user_id)})
            if user_data:
                from .models import User
                return User(user_data)
            return None
        except:
            return None
    
    return app
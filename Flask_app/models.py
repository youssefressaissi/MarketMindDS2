from flask_login import UserMixin
from bson.objectid import ObjectId
from datetime import datetime
from werkzeug.security import generate_password_hash
from . import mongo

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.email = user_data['email']
        self.password = user_data['password']
        self.first_name = user_data['first_name']
        self.created_at = user_data.get('created_at', datetime.utcnow())

    @staticmethod
    def get_by_email(email):
        user_data = mongo.db.users.find_one({"email": email.lower().strip()})
        return User(user_data) if user_data else None

    @staticmethod
    def create(email, first_name, password):
        user_data = {
            "email": email.lower().strip(),
            "first_name": first_name.strip(),
            "password": generate_password_hash(password, method='pbkdf2:sha256'),
            "created_at": datetime.utcnow()
        }
        result = mongo.db.users.insert_one(user_data)
        return User.get_by_id(str(result.inserted_id))

    @staticmethod
    def get_by_id(user_id):
        if not ObjectId.is_valid(user_id):
            return None
        user_data = mongo.db.users.find_one({"_id": ObjectId(user_id)})
        return User(user_data) if user_data else None
from flask import Blueprint, render_template
from flask_login import login_required, current_user  # Added missing import

views = Blueprint('views', __name__)

@views.route('/')
def home():
    """Public landing page"""
    return render_template("home.html", user=current_user)

@views.route('/dashboard')
@login_required  # Now properly imported
def dashboard():
    """Authenticated user dashboard"""
    return render_template("dashboard.html", user=current_user)
# flask_app/Dockerfile

FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Set environment variables for Flask CLI
# Point to the create_app factory function inside __init__.py
ENV FLASK_APP=__init__:create_app
# Optional: Set default debug mode
ENV FLASK_DEBUG=True

EXPOSE 5000

# Use the Flask command line interface to run the app
CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]
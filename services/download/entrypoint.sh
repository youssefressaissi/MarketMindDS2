#!/bin/bash

set -Eeuo pipefail

# Function to show progress
show_progress() {
    local pid=$1
    local delay=0.5
    local spinstr='|/-\'
    while [ "$(ps a | awk '{print $1}' | grep $pid)" ]; do
        local temp=${spinstr#?}
        printf " [%c]  " "$spinstr"
        local spinstr=$temp${spinstr%"$temp"}
        sleep $delay
        printf "\b\b\b\b\b\b"
    done
    printf "    \b\b\b\b"
}

# Create necessary directories
echo "Creating directories..."
mkdir -p /data/models/xtts
mkdir -p /data/xtts/actors
mkdir -p /data/xtts/output_audio

# Install required packages efficiently
echo "Installing required packages..."
apt-get update -qq && \
apt-get install -y -qq aria2 curl && \
rm -rf /var/lib/apt/lists/*

# Download XTTS model files
echo "Downloading XTTS model files..."
cd /data/models/xtts

# Create a download list file for aria2
cat > download.lst << EOF
https://huggingface.co/coqui/XTTS-v2/resolve/main/model.pth
  out=model.pth
  max-connection-per-server=16
  split=16
  min-split-size=1M
  max-tries=3
  timeout=300
  continue=true
https://huggingface.co/coqui/XTTS-v2/resolve/main/config.json
  out=config.json
  max-tries=3
  timeout=60
  continue=true
https://huggingface.co/coqui/XTTS-v2/resolve/main/vocab.json
  out=vocab.json
  max-tries=3
  timeout=60
  continue=true
EOF

# Start downloads with progress tracking
echo "Starting downloads..."
aria2c -i download.lst --console-log-level=warn --summary-interval=5 --auto-file-renaming=false &
show_progress $!

# Wait for downloads to complete
wait

# Verify the downloads
echo "Verifying downloads..."
if [ ! -f "model.pth" ] || [ ! -f "config.json" ] || [ ! -f "vocab.json" ]; then
    echo "Error: Failed to download required model files"
    exit 1
fi

echo "XTTS model files downloaded successfully"

# Create a sample speaker file (optional)
echo "Creating sample speaker directory..."
mkdir -p /data/xtts/actors/sample
touch /data/xtts/actors/sample/sample.wav

echo "Setup completed successfully" 

rm -rf data/models/xtts/* 
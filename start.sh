#!/bin/bash
# GC Autologger Launcher

# Navigate to the script's directory
cd "$(dirname "$0")"

# If virtual environment doesn't exist, create it and install dependencies
if [ ! -f "venv/bin/python3" ] && [ ! -f "venv/bin/python" ]; then
    echo "First-time setup: Initializing virtual environment and installing dependencies..."
    python3 -m venv venv
    venv/bin/pip install -r requirements.txt
fi

# Prefer virtual environment python
if [ -f "venv/bin/python3" ]; then
    PYTHON_CMD="venv/bin/python3"
elif [ -f "venv/bin/python" ]; then
    PYTHON_CMD="venv/bin/python"
else
    PYTHON_CMD="python3"
fi

exec "$PYTHON_CMD" main.py "$@"

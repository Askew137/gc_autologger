#!/bin/bash
# GC Autologger Launcher

# Navigate to the script's directory
cd "$(dirname "$0")"

# Prefer system python3 if dependencies are already satisfied, otherwise check venv
if python3 -c "import customtkinter, requests, PIL" 2>/dev/null; then
    PYTHON_CMD="python3"
elif [ -f "venv/bin/python3" ]; then
    PYTHON_CMD="venv/bin/python3"
elif [ -f "venv/bin/python" ]; then
    PYTHON_CMD="venv/bin/python"
else
    echo "First-time setup: Initializing virtual environment and installing dependencies..."
    python3 -m venv --system-site-packages venv
    venv/bin/pip install -r requirements.txt
    PYTHON_CMD="venv/bin/python3"
fi

exec "$PYTHON_CMD" main.py "$@"

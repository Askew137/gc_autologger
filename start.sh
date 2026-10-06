#!/bin/bash
# GC Autologger Launcher

# Navigate to the script's directory
cd "$(dirname "$0")"

# Prefer virtual environment python if it exists
if [ -f "venv/bin/python3" ]; then
    PYTHON_CMD="venv/bin/python3"
elif [ -f "venv/bin/python" ]; then
    PYTHON_CMD="venv/bin/python"
else
    PYTHON_CMD="python3"
fi

exec "$PYTHON_CMD" main.py "$@"

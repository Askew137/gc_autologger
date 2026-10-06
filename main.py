#!/usr/bin/env python3
"""
GC Autologger
Main application entry point.

Usage:
    python3 main.py          # Launches modern CustomTkinter GUI
    python3 main.py --cli    # Runs interactive Command-Line Interface
"""

import sys
import os

# Ensure project root is in Python module search path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def main():
    if "--cli" in sys.argv:
        from cli import run_cli
        run_cli()
        return

    # Attempt launching CustomTkinter GUI
    try:
        from gui.app import UltimateApp
        app = UltimateApp()
        app.run()
    except ImportError as e:
        print(f"\n[Notice] GUI dependencies not fully installed: {e}")
        print("Falling back to interactive terminal mode (CLI).\n")
        print("To enable the modern GUI, install dependencies:")
        print("    pip install -r requirements.txt\n")
        from cli import run_cli
        run_cli()


if __name__ == "__main__":
    main()

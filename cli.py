"""
Interactive Command-Line Interface (CLI) for GC Autologger.
Allows running all operations directly from the terminal without GUI dependencies.
"""

import os
import sys
from datetime import datetime
from typing import List

from core.config import ConfigManager
from core.client import GeocachingClient
from core.safety import SafetyManager
from core.gpx_parser import parse_file, parse_folder, extract_gc_codes_from_string
from operations.coordinate_uploader import CoordinateUploader
from operations.cache_logger import CacheLogger, LOG_TYPE_NAMES
from operations.user_copy import UserLogCopier


def run_cli():
    """Main CLI entrypoint."""
    config_mgr = ConfigManager()
    config = config_mgr.config

    print("\n" + "=" * 60)
    print("      GC Autologger (CLI mode)")
    print("      Direct HTTP API Engine powered by c:geo logic")
    print("=" * 60)

    # 1. Select Account
    if not config.accounts:
        print("\n❌ No accounts configured in config.json. Please add an account first.")
        user = input("Enter Geocaching Username: ").strip()
        pwd = input("Enter Geocaching Password: ").strip()
        if not user or not pwd:
            print("Aborted.")
            return
        config_mgr.add_or_update_account(user, pwd, is_default=True)
        config = config_mgr.config

    active_acc = config_mgr.get_default_account()
    if len(config.accounts) > 1:
        print("\nSelect account:")
        for idx, acc in enumerate(config.accounts, 1):
            def_mark = " (Default)" if acc.is_default else ""
            print(f"{idx}. {acc.username}{def_mark}")
        choice = input(f"Enter account number [1-{len(config.accounts)}] (or press Enter for default): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(config.accounts):
            active_acc = config.accounts[int(choice) - 1]

    print(f"\n[Active Account] {active_acc.username}")

    # Initialize client & safety
    safety = SafetyManager(
        min_delay=config.safety.min_delay_seconds,
        max_delay=config.safety.max_delay_seconds,
        breather_interval=config.safety.breather_interval,
        breather_duration=config.safety.breather_duration_seconds
    )
    client = GeocachingClient()

    print("Authenticating with Geocaching.com...")
    if client.is_logged_in() and client.logged_in_username == active_acc.username:
        print("✅ Session restored from saved cookies.")
    else:
        success, msg = client.login(active_acc.username, active_acc.password)
        if not success:
            print(f"❌ Login failed: {msg}")
            return
        print(f"✅ {msg}")

    # 2. Select Mode
    menu = [
        ("FOUND", "Log Caches: Found it"),
        ("DNF", "Log Caches: Didn't find it (DNF)"),
        ("NOTE", "Log Caches: Write note"),
        ("NEEDS_OWNER_ATTENTION", "Log Caches: Needs maintenance"),
        ("NEEDS_REVIEWER_ATTENTION", "Log Caches: Needs archive"),
        ("COPY_USER", "Copy logs from another user"),
        ("COORDS_ALL", "Upload Coordinates from GPX (Complete overwrite)"),
        ("COORDS_UNMODIFIED", "Upload Coordinates from GPX (Only unmodified caches)"),
        ("IGNORE", "Bulk Add to Ignore List"),
    ]

    print("\nSelect Operation:")
    for idx, (_, name) in enumerate(menu, 1):
        print(f"{idx}. {name}")

    while True:
        m_choice = input(f"Enter choice [1-{len(menu)}]: ").strip()
        if m_choice.isdigit() and 1 <= int(m_choice) <= len(menu):
            selected_mode = menu[int(m_choice) - 1][0]
            break
        print("Invalid choice.")

    # 3. Handle Coordinate Upload
    if selected_mode in ["COORDS_ALL", "COORDS_UNMODIFIED"]:
        uploader = CoordinateUploader(client, safety)
        upload_mode = (
            CoordinateUploader.MODE_OVERWRITE_ALL
            if selected_mode == "COORDS_ALL"
            else CoordinateUploader.MODE_UNMODIFIED_ONLY
        )

        path = input(f"\nEnter GPX file or folder path (or press Enter for default '{config.folder_path}'): ").strip()
        if not path:
            path = config.folder_path
        if not path or not os.path.exists(path):
            print(f"❌ Path does not exist: {path}")
            return

        if os.path.isfile(path):
            items = parse_file(path)
        else:
            items = parse_folder(path)

        if not items:
            print("No waypoints found in given source.")
            return

        print(f"\nLoaded {len(items)} caches. Starting upload...")
        uploader.run(
            items,
            mode=upload_mode,
            on_log=lambda m, lvl: print(f"[{lvl.upper()}] {m}"),
            omit_virtual_and_earth=config.filters.omit_virtual_and_earth
        )
        return

    # 4. Handle Bulk Logging
    if selected_mode in ["FOUND", "DNF", "NOTE", "NEEDS_OWNER_ATTENTION", "NEEDS_REVIEWER_ATTENTION"]:
        logger = CacheLogger(client, safety)

        print("\nChoose input method:")
        print("1. Load from GPX file")
        print("2. Enter GC codes manually")
        in_method = input("Enter choice [1-2]: ").strip()

        codes: List[str] = []
        if in_method == "1":
            f_path = input("Enter GPX file path: ").strip()
            if os.path.isfile(f_path):
                items = parse_file(f_path)
                codes = [it.gccode for it in items]
        else:
            raw_codes = input("Enter GC codes (comma/space separated): ").strip()
            codes = extract_gc_codes_from_string(raw_codes)

        if not codes:
            print("No GC codes to process.")
            return

        date_str = input(f"Enter date [YYYY-MM-DD] (Enter for today {datetime.now().strftime('%Y-%m-%d')}): ").strip()
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        print("\nSelect log template:")
        for idx, t in enumerate(config.log_templates, 1):
            print(f"{idx}. {t[:50]}...")
        print(f"{len(config.log_templates) + 1}. [Custom text]")

        t_choice = input(f"Enter choice [1-{len(config.log_templates) + 1}]: ").strip()
        if t_choice.isdigit() and 1 <= int(t_choice) <= len(config.log_templates):
            log_text = config.log_templates[int(t_choice) - 1]
        else:
            log_text = input("Enter log text: ").strip()

        logger.run_log(
            codes,
            log_type_key=selected_mode,
            date_str=date_str,
            log_text=log_text,
            on_log=lambda m, lvl: print(f"[{lvl.upper()}] {m}"),
            omit_virtual_and_earth=config.filters.omit_virtual_and_earth
        )
        return

    # 5. Handle Copy User
    if selected_mode == "COPY_USER":
        copier = UserLogCopier(client, safety)
        t_user = input("\nEnter target username to copy from: ").strip()
        t_pwd = input("Optional password (press Enter if public account): ").strip() or None
        t_date = input(f"Target date [YYYY-MM-DD] (Enter for today {datetime.now().strftime('%Y-%m-%d')}): ").strip()
        if not t_date:
            t_date = datetime.now().strftime("%Y-%m-%d")

        codes = copier.fetch_user_logged_caches(t_user, t_pwd, t_date, on_log=lambda m, lvl: print(f"[{lvl.upper()}] {m}"))
        if not codes:
            print("No caches found to copy.")
            return

        confirm = input(f"Found {len(codes)} caches. Log all as 'Found it' on {active_acc.username}? [y/N]: ").strip().lower()
        if confirm == 'y':
            log_text = config.log_templates[0] if config.log_templates else "Found it! TFTC"
            logger = CacheLogger(client, safety)
            logger.run_log(
                codes,
                log_type_key="FOUND",
                date_str=t_date,
                log_text=log_text,
                on_log=lambda m, lvl: print(f"[{lvl.upper()}] {m}")
            )
        return

    # 6. Handle Ignore List
    if selected_mode == "IGNORE":
        logger = CacheLogger(client, safety)
        print("\nChoose input method for Ignore List:")
        print("1. Load from GPX / LOC file")
        print("2. Load all files from folder")
        print("3. Enter GC codes manually")
        in_choice = input("Enter choice [1-3]: ").strip()

        codes: List[str] = []
        if in_choice == "1":
            p = input("Enter file path: ").strip()
            if os.path.isfile(p):
                codes = [it.gccode for it in parse_file(p)]
        elif in_choice == "2":
            p = input(f"Enter folder path (or Enter for default '{config.folder_path}'): ").strip() or config.folder_path
            if os.path.isdir(p):
                codes = [it.gccode for it in parse_folder(p)]
        else:
            raw_codes = input("\nEnter GC codes (comma/space separated): ").strip()
            codes = extract_gc_codes_from_string(raw_codes)

        if not codes:
            print("No GC codes to ignore.")
            return

        print(f"\nLoaded {len(codes)} caches to ignore.")
        confirm = input(f"Are you sure you want to add {len(codes)} caches to Ignore List? [y/N]: ").strip().lower()
        if confirm == 'y':
            logger.run_ignore(codes, on_log=lambda m, lvl: print(f"[{lvl.upper()}] {m}"))


if __name__ == "__main__":
    run_cli()

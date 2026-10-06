"""
Graphical User Interface for GC AutoLogger Ultimate using CustomTkinter.
Features modern dark styling, thread-safe asynchronous operations,
interactive config/accounts manager, real-time logging console, and progress tracking.
"""

import os
import sys
import time
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

try:
    import customtkinter as ctk
    import tkinter as tk
    from tkinter import filedialog, messagebox
    CTK_AVAILABLE = True
except ImportError:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    ctk = None
    CTK_AVAILABLE = False

from core.config import ConfigManager, AccountConfig
from core.client import GeocachingClient
from core.safety import SafetyManager
from core.checkpoint import RunCheckpointManager
from core.gpx_parser import parse_file, parse_folder, extract_gc_codes_from_string, WaypointItem
from operations.coordinate_uploader import CoordinateUploader
from operations.cache_logger import CacheLogger, LOG_TYPE_MAP, LOG_TYPE_NAMES
from operations.user_copy import UserLogCopier
import gui.theme as theme


class UltimateApp:
    """Main graphical application class."""

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        if not CTK_AVAILABLE:
            self._show_missing_ctk_dialog()
            return

        self.config_mgr = config_manager or ConfigManager()
        self.config = self.config_mgr.config

        # Setup CustomTkinter appearance
        ctk.set_appearance_mode(self.config.gui.theme)
        ctk.set_default_color_theme(self.config.gui.color_theme)

        self.root = ctk.CTk()
        self.root.title("GC Autologger")
        self.root.geometry("1050x780")
        self.root.minsize(920, 680)

        # Core components
        self.client: Optional[GeocachingClient] = None
        self.safety = SafetyManager(
            min_delay=self.config.safety.min_delay_seconds,
            max_delay=self.config.safety.max_delay_seconds,
            breather_interval=self.config.safety.breather_interval,
            breather_duration=self.config.safety.breather_duration_seconds
        )
        self.checkpoint_mgr = RunCheckpointManager()

        self.loaded_waypoints: List[WaypointItem] = []
        self.is_running = False

        self._init_ui()
        self._set_app_icon()
        self._init_client_session()
        self._check_active_checkpoint()

    def _set_app_icon(self):
        """Set window icon and macOS Dock icon if assets exist."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        png_path = os.path.join(base_dir, "assets", "icon.png")
        ico_path = os.path.join(base_dir, "assets", "icon.ico")

        # 1. macOS Dock icon via AppKit / Cocoa (prefers native multi-res .icns)
        if sys.platform == "darwin":
            icns_path = os.path.join(base_dir, "assets", "icon.icns")
            icon_file = icns_path if os.path.exists(icns_path) else png_path
            if os.path.exists(icon_file):
                try:
                    from AppKit import NSApplication, NSImage
                    app = NSApplication.sharedApplication()
                    icon = NSImage.alloc().initWithContentsOfFile_(icon_file)
                    if icon:
                        app.setApplicationIconImage_(icon)
                except Exception:
                    pass

        # 2. Window titlebar & taskbar icon (Windows / Linux)
        if os.path.exists(ico_path) and sys.platform == "win32":
            try:
                self.root.iconbitmap(ico_path)
            except Exception:
                pass
        elif os.path.exists(png_path):
            try:
                from PIL import Image, ImageTk
                im = Image.open(png_path).resize((64, 64))
                self._icon_photo = ImageTk.PhotoImage(im)
                self.root.wm_iconphoto(True, self._icon_photo)
            except Exception:
                pass

    def _show_missing_ctk_dialog(self):
        """Fallback dialog when customtkinter is not yet installed."""
        root = tk.Tk()
        root.title("GC Autologger - setup required")
        root.geometry("520x240")
        root.configure(bg="#1a1a1a")

        lbl = tk.Label(
            root,
            text="CustomTkinter is required for the modern GUI.",
            fg="#ffffff",
            bg="#1a1a1a",
            font=("Helvetica", 15, "bold"),
            pady=15
        )
        lbl.pack()

        lbl_info = tk.Label(
            root,
            text="Please run the following command in your terminal:\n\npip install -r requirements.txt\n(or: pip install customtkinter requests Pillow)",
            fg="#a0a0a0",
            bg="#1a1a1a",
            font=("Helvetica", 12),
            justify="center",
            pady=10
        )
        lbl_info.pack()

        btn = tk.Button(
            root,
            text="OK / Exit",
            command=root.destroy,
            width=15,
            bg="#2a2a2a",
            fg="#ffffff"
        )
        btn.pack(pady=15)
        root.mainloop()
        sys.exit(1)

    # --------------------------------------------------------------------------
    # UI Layout Construction
    # --------------------------------------------------------------------------

    def _init_ui(self):
        # 1. Header Frame (Accounts & Connection status)
        self.header_frame = ctk.CTkFrame(self.root, height=65, corner_radius=0, fg_color=theme.HEADER_BG)
        self.header_frame.pack(fill="x", side="top")
        self._build_header()

        # 2. Main Tab View
        self.tabview = ctk.CTkTabview(self.root, corner_radius=8, command=self._on_tab_changed)
        self.tabview.pack(fill="both", expand=True, padx=14, pady=(10, 5))

        self.tab_logger = self.tabview.add("📝 Bulk log caches")
        self.tab_user_copy = self.tabview.add("👥 Copy user")
        self.tab_coords = self.tabview.add("📍 Upload coordinates")
        self.tab_ignore = self.tabview.add("🚫 Ignore list")
        self.tab_settings = self.tabview.add("⚙️ Config & accounts")

        self._build_tab_coords()
        self._build_tab_logger()
        self._build_tab_user_copy()
        self._build_tab_ignore()
        self._build_tab_settings()

        # 3. Bottom Frame (Progress bar & Console Log)
        self.bottom_frame = ctk.CTkFrame(self.root, height=220, corner_radius=8, fg_color=theme.CARD_BG)
        self.bottom_frame.pack(fill="x", side="bottom", padx=14, pady=(5, 12))
        self._build_bottom_panel()

    def _on_tab_changed(self):
        self._check_active_checkpoint()

    def _build_header(self):
        # Logo / Title
        title_lbl = ctk.CTkLabel(
            self.header_frame,
            text="GC Autologger",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        title_lbl.pack(side="left", padx=18, pady=12)

        # Connection status badge
        self.status_badge = ctk.CTkLabel(
            self.header_frame,
            text="● Connecting...",
            text_color=theme.ACCENT_YELLOW,
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.status_badge.pack(side="right", padx=18)

        # Login button
        self.btn_relogin = ctk.CTkButton(
            self.header_frame,
            text="Login / re-auth",
            width=110,
            height=30,
            command=self._on_click_login
        )
        self.btn_relogin.pack(side="right", padx=8)

        # Account selector
        acc_names = [a.username for a in self.config.accounts] or ["No accounts"]
        self.acc_combo = ctk.CTkComboBox(
            self.header_frame,
            values=acc_names,
            width=170,
            command=self._on_account_switched
        )
        self.acc_combo.pack(side="right", padx=8)

        acc_lbl = ctk.CTkLabel(self.header_frame, text="Active account:", text_color=theme.TEXT_MUTED)
        acc_lbl.pack(side="right", padx=4)

    # --------------------------------------------------------------------------
    # Tab 1: Coordinates Upload
    # --------------------------------------------------------------------------

    def _build_tab_coords(self):
        # File selection bar
        file_frame = ctk.CTkFrame(self.tab_coords, fg_color="transparent")
        file_frame.pack(fill="x", pady=8, padx=6)

        ctk.CTkLabel(file_frame, text="GPX / LOC source:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=5)

        self.coords_path_entry = ctk.CTkEntry(file_frame, placeholder_text="Select GPX/LOC file or folder...")
        self.coords_path_entry.pack(side="left", fill="x", expand=True, padx=8)
        if self.config.folder_path:
            self.coords_path_entry.insert(0, self.config.folder_path)

        ctk.CTkButton(file_frame, text="Browse file", width=95, command=self._browse_coords_file).pack(side="left", padx=4)
        ctk.CTkButton(file_frame, text="Browse folder", width=105, command=self._browse_coords_folder).pack(side="left", padx=4)
        ctk.CTkButton(file_frame, text="Load items", width=90, fg_color=theme.ACCENT_BLUE, command=self._load_coords_items).pack(side="left", padx=4)

        # Resume banner frame (shown if an unfinished run is detected)
        self.frame_resume_coords = ctk.CTkFrame(
            self.tab_coords,
            fg_color="#2b2613",
            border_width=1,
            border_color="#d48806",
            corner_radius=6
        )
        self.lbl_resume_info = ctk.CTkLabel(
            self.frame_resume_coords,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#f5c056"
        )
        self.lbl_resume_info.pack(side="left", padx=12, pady=8)

        self.btn_discard_action = ctk.CTkButton(
            self.frame_resume_coords,
            text="✕ Discard",
            width=75,
            height=28,
            fg_color=theme.ACCENT_RED,
            command=self._on_discard_coords_clicked
        )
        self.btn_discard_action.pack(side="right", padx=8, pady=6)

        self.btn_resume_action = ctk.CTkButton(
            self.frame_resume_coords,
            text="▶ Resume upload",
            width=125,
            height=28,
            fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            command=self._on_resume_coords_clicked
        )
        self.btn_resume_action.pack(side="right", padx=4, pady=6)

        # Mode & options frame
        self.coords_opt_frame = ctk.CTkFrame(self.tab_coords)
        self.coords_opt_frame.pack(fill="x", pady=6, padx=6)

        ctk.CTkLabel(self.coords_opt_frame, text="Upload mode:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=10, pady=8, sticky="w")

        self.coords_mode_var = ctk.StringVar(value=CoordinateUploader.MODE_OVERWRITE_ALL)
        ctk.CTkRadioButton(
            self.coords_opt_frame,
            text="Complete overwrite (upload all coordinates in file)",
            variable=self.coords_mode_var,
            value=CoordinateUploader.MODE_OVERWRITE_ALL
        ).grid(row=0, column=1, padx=10, pady=8, sticky="w")

        ctk.CTkRadioButton(
            self.coords_opt_frame,
            text="Only unmodified (skip caches already corrected on web)",
            variable=self.coords_mode_var,
            value=CoordinateUploader.MODE_UNMODIFIED_ONLY
        ).grid(row=0, column=2, padx=15, pady=8, sticky="w")


        # Summary / Queue label
        self.lbl_coords_summary = ctk.CTkLabel(
            self.tab_coords,
            text="No GPX file loaded yet. Choose a file or folder above.",
            font=ctk.CTkFont(size=13),
            text_color=theme.TEXT_MUTED
        )
        self.lbl_coords_summary.pack(anchor="w", padx=10, pady=(6, 4))

        # Controls (Start / Pause / Cancel)
        ctrl_frame = ctk.CTkFrame(self.tab_coords, fg_color="transparent")
        ctrl_frame.pack(fill="x", pady=8, padx=6)

        self.btn_coords_start = ctk.CTkButton(
            ctrl_frame,
            text="▶ Start upload",
            font=ctk.CTkFont(weight="bold"),
            fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            height=36,
            command=self._start_coords_upload
        )
        self.btn_coords_start.pack(side="left", padx=6)

        self.btn_coords_pause = ctk.CTkButton(
            ctrl_frame,
            text="⏸ Pause",
            width=90,
            height=36,
            state="disabled",
            command=self._toggle_pause
        )
        self.btn_coords_pause.pack(side="left", padx=6)

        self.btn_coords_stop = ctk.CTkButton(
            ctrl_frame,
            text="⏹ Stop",
            width=90,
            height=36,
            fg_color=theme.ACCENT_RED,
            state="disabled",
            command=self._stop_operation
        )
        self.btn_coords_stop.pack(side="left", padx=6)

    # --------------------------------------------------------------------------
    # Tab 2: Bulk Log Caches
    # --------------------------------------------------------------------------

    def _build_tab_logger(self):
        log_grid = ctk.CTkFrame(self.tab_logger, fg_color="transparent")
        log_grid.pack(fill="both", expand=True, padx=8, pady=8)

        # Left side: inputs
        left_box = ctk.CTkFrame(log_grid)
        left_box.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        # Log Type
        ctk.CTkLabel(left_box, text="Log type:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=10, pady=8, sticky="w")
        type_options = list(LOG_TYPE_NAMES.values())
        self.combo_log_type = ctk.CTkComboBox(left_box, values=type_options, width=200)
        self.combo_log_type.set("Found it")
        self.combo_log_type.grid(row=0, column=1, padx=10, pady=8, sticky="w")

        # Date
        ctk.CTkLabel(left_box, text="Log date (DD-MM-YYYY):", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=10, pady=8, sticky="w")
        date_box = ctk.CTkFrame(left_box, fg_color="transparent")
        date_box.grid(row=1, column=1, padx=10, pady=8, sticky="w")
        self.entry_date = ctk.CTkEntry(date_box, width=120)
        self.entry_date.insert(0, datetime.now().strftime("%d-%m-%Y"))
        self.entry_date.pack(side="left")
        ctk.CTkButton(date_box, text="Today", width=55, command=lambda: self._set_date_today(self.entry_date)).pack(side="left", padx=3)
        ctk.CTkButton(date_box, text="▼", width=28, command=lambda: self._step_date(self.entry_date, -1)).pack(side="left", padx=2)
        ctk.CTkButton(date_box, text="▲", width=28, command=lambda: self._step_date(self.entry_date, 1)).pack(side="left", padx=2)

        # Templates
        ctk.CTkLabel(left_box, text="Template:", font=ctk.CTkFont(weight="bold")).grid(row=2, column=0, padx=10, pady=8, sticky="w")
        templates = self.config.log_templates or ["Thanks for the cache!"]
        self.combo_template = ctk.CTkComboBox(left_box, values=templates, width=320, command=self._on_template_selected)
        self.combo_template.grid(row=2, column=1, padx=10, pady=8, sticky="w")

        # Log Text Box
        ctk.CTkLabel(left_box, text="Log text:", font=ctk.CTkFont(weight="bold")).grid(row=3, column=0, padx=10, pady=4, sticky="nw")
        self.txt_log_message = ctk.CTkTextbox(left_box, height=120)
        self.txt_log_message.grid(row=3, column=1, padx=10, pady=4, sticky="nsew")
        if templates:
            self.txt_log_message.insert("1.0", templates[0])

        # Right side: Target GC codes
        right_box = ctk.CTkFrame(log_grid)
        right_box.pack(side="right", fill="both", expand=True, padx=5, pady=5)

        ctk.CTkLabel(right_box, text="Target GC codes (from GPX or paste here):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=6)
        self.txt_gc_codes = ctk.CTkTextbox(right_box, height=170)
        self.txt_gc_codes.pack(fill="both", expand=True, padx=10, pady=4)

        btn_row = ctk.CTkFrame(right_box, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=6)
        ctk.CTkButton(btn_row, text="Import from GPX", width=120, command=self._import_codes_from_file).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="Clear codes", width=90, fg_color=theme.CARD_BORDER, command=lambda: self.txt_gc_codes.delete("1.0", "end")).pack(side="left", padx=4)

        # Start Log Button
        self.btn_log_start = ctk.CTkButton(
            self.tab_logger,
            text="▶ Start bulk logging",
            font=ctk.CTkFont(weight="bold"),
            fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            height=36,
            command=self._start_bulk_logging
        )
        self.btn_log_start.pack(anchor="w", padx=14, pady=8)

    # --------------------------------------------------------------------------
    # Tab 3: Copy User
    # --------------------------------------------------------------------------

    def _build_tab_user_copy(self):
        box = ctk.CTkFrame(self.tab_user_copy)
        box.pack(fill="both", expand=True, padx=14, pady=14)

        ctk.CTkLabel(box, text="Log the same caches as another user on a specific date", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=14, pady=(12, 6))

        grid = ctk.CTkFrame(box, fg_color="transparent")
        grid.pack(anchor="w", padx=14, pady=8)

        # Select target account from configured accounts or custom
        ctk.CTkLabel(grid, text="Select account:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        active_user = self.acc_combo.get().strip() if hasattr(self, "acc_combo") else ""
        other_accounts = ["[Custom user...]"] + [a.username for a in self.config.accounts if a.username != active_user]
        self.combo_copy_account = ctk.CTkComboBox(
            grid,
            values=other_accounts,
            width=220,
            command=self._on_copy_account_selected
        )
        self.combo_copy_account.grid(row=0, column=1, padx=6, pady=6, sticky="w")
        self.combo_copy_account.set("[Custom user...]")

        self.var_password_mode = ctk.BooleanVar(value=False)
        self.chk_password_mode = ctk.CTkCheckBox(
            grid,
            text="Password mode",
            variable=self.var_password_mode,
            command=self._toggle_password_mode
        )
        self.chk_password_mode.grid(row=0, column=2, padx=14, pady=6, sticky="w")

        self.lbl_password_mode_hint = ctk.CTkLabel(
            grid,
            text="💡 Use “Password mode” when target account was not made public or when you want to copy premium caches as a non-premium member.",
            font=ctk.CTkFont(size=11),
            text_color=theme.TEXT_MUTED
        )
        self.lbl_password_mode_hint.grid(row=1, column=0, columnspan=3, padx=6, pady=(0, 6), sticky="w")

        ctk.CTkLabel(grid, text="Target username:").grid(row=2, column=0, padx=6, pady=6, sticky="w")
        self.entry_copy_user = ctk.CTkEntry(grid, width=220, placeholder_text="Username to copy from")
        self.entry_copy_user.grid(row=2, column=1, padx=6, pady=6, sticky="w")

        # Target Password (hidden unless Password Mode is checked)
        self.lbl_copy_pwd = ctk.CTkLabel(grid, text="Target password:")
        self.entry_copy_pwd = ctk.CTkEntry(grid, width=220, show="*", placeholder_text="(Account password)")

        ctk.CTkLabel(grid, text="Date (DD-MM-YYYY):").grid(row=4, column=0, padx=6, pady=6, sticky="w")
        date_box = ctk.CTkFrame(grid, fg_color="transparent")
        date_box.grid(row=4, column=1, padx=6, pady=6, sticky="w")
        self.entry_copy_date = ctk.CTkEntry(date_box, width=120)
        self.entry_copy_date.insert(0, datetime.now().strftime("%d-%m-%Y"))
        self.entry_copy_date.pack(side="left")
        ctk.CTkButton(
            date_box,
            text="Today",
            width=55,
            command=lambda: self._set_date_today(self.entry_copy_date)
        ).pack(side="left", padx=3)
        ctk.CTkButton(
            date_box,
            text="▼",
            width=28,
            command=lambda: self._step_date(self.entry_copy_date, -1)
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            date_box,
            text="▲",
            width=28,
            command=lambda: self._step_date(self.entry_copy_date, 1)
        ).pack(side="left", padx=2)

        # Trigger initial selection fill if accounts exist
        self._on_copy_account_selected(self.combo_copy_account.get())

        ctk.CTkButton(box, text="Fetch & prepare logs", fg_color=theme.ACCENT_BLUE, command=self._start_copy_user_fetch).pack(anchor="w", padx=14, pady=10)

    # --------------------------------------------------------------------------
    # Tab 4: Ignore List
    # --------------------------------------------------------------------------

    def _build_tab_ignore(self):
        box = ctk.CTkFrame(self.tab_ignore)
        box.pack(fill="both", expand=True, padx=14, pady=14)

        ctk.CTkLabel(box, text="Bulk add caches to ignore list", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=14, pady=(12, 6))

        # File & Folder Import row
        file_frame = ctk.CTkFrame(box, fg_color="transparent")
        file_frame.pack(fill="x", padx=14, pady=6)

        ctk.CTkLabel(file_frame, text="Import from GPX/LOC:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=4)
        self.ignore_path_entry = ctk.CTkEntry(file_frame, placeholder_text="Select GPX/LOC file or folder...")
        self.ignore_path_entry.pack(side="left", fill="x", expand=True, padx=8)

        ctk.CTkButton(file_frame, text="Browse file", width=95, command=self._browse_ignore_file).pack(side="left", padx=3)
        ctk.CTkButton(file_frame, text="Browse folder", width=105, command=self._browse_ignore_folder).pack(side="left", padx=3)
        ctk.CTkButton(file_frame, text="Import to list", width=100, fg_color=theme.ACCENT_BLUE, command=self._import_ignore_file).pack(side="left", padx=3)

        # Text input & review
        ctk.CTkLabel(box, text="GC codes to ignore (from imported files or paste here):", text_color=theme.TEXT_MUTED).pack(anchor="w", padx=14, pady=(6, 2))

        self.txt_ignore_codes = ctk.CTkTextbox(box, height=160)
        self.txt_ignore_codes.pack(fill="both", expand=True, padx=14, pady=4)

        btn_row = ctk.CTkFrame(box, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=8)

        ctk.CTkButton(
            btn_row,
            text="🚫 Add caches to ignore list",
            font=ctk.CTkFont(weight="bold"),
            fg_color=theme.ACCENT_RED,
            height=36,
            command=self._start_bulk_ignore
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_row,
            text="Clear codes",
            width=90,
            fg_color=theme.CARD_BORDER,
            command=lambda: self.txt_ignore_codes.delete("1.0", "end")
        ).pack(side="left", padx=6)

    # --------------------------------------------------------------------------
    # Tab 5: Config & Accounts (Visual Settings Editor)
    # --------------------------------------------------------------------------

    def _build_tab_settings(self):
        scroll = ctk.CTkScrollableFrame(self.tab_settings)
        scroll.pack(fill="both", expand=True, padx=6, pady=6)

        # 1. Accounts Section
        acc_sec = ctk.CTkFrame(scroll)
        acc_sec.pack(fill="x", pady=6, padx=6)
        ctk.CTkLabel(acc_sec, text="Geocaching accounts", font=ctk.CTkFont(weight="bold", size=14)).pack(anchor="w", padx=12, pady=(10, 4))

        # Accounts Listbox/View
        self.acc_display_frame = ctk.CTkFrame(acc_sec, fg_color="transparent")
        self.acc_display_frame.pack(fill="x", padx=12, pady=4)
        self._refresh_accounts_view()

        # Add Account Controls
        add_frame = ctk.CTkFrame(acc_sec, fg_color="transparent")
        add_frame.pack(fill="x", padx=12, pady=(8, 12))

        self.new_acc_user = ctk.CTkEntry(add_frame, placeholder_text="Username", width=160)
        self.new_acc_user.pack(side="left", padx=4)

        self.new_acc_pwd = ctk.CTkEntry(add_frame, placeholder_text="Password (optional)", show="*", width=160)
        self.new_acc_pwd.pack(side="left", padx=4)

        self.new_acc_default = ctk.CTkCheckBox(add_frame, text="Set as default", width=120)
        self.new_acc_default.pack(side="left", padx=8)

        ctk.CTkButton(add_frame, text="+ Add account", width=110, command=self._on_add_account).pack(side="left", padx=4)

        # 2. Log Templates Section
        tmpl_sec = ctk.CTkFrame(scroll)
        tmpl_sec.pack(fill="x", pady=6, padx=6)
        ctk.CTkLabel(tmpl_sec, text="Log templates", font=ctk.CTkFont(weight="bold", size=14)).pack(anchor="w", padx=12, pady=(10, 4))

        self.tmpl_display_frame = ctk.CTkFrame(tmpl_sec, fg_color="transparent")
        self.tmpl_display_frame.pack(fill="x", padx=12, pady=4)
        self._refresh_templates_view()

        add_tmpl_frame = ctk.CTkFrame(tmpl_sec, fg_color="transparent")
        add_tmpl_frame.pack(fill="x", padx=12, pady=(8, 12))

        self.new_tmpl_entry = ctk.CTkEntry(add_tmpl_frame, placeholder_text="New log template text...", width=360)
        self.new_tmpl_entry.pack(side="left", padx=4)

        ctk.CTkButton(add_tmpl_frame, text="+ Add log template", width=140, command=self._on_add_template).pack(side="left", padx=4)

        # 3. Safety & Delays Section
        safety_sec = ctk.CTkFrame(scroll)
        safety_sec.pack(fill="x", pady=6, padx=6)
        ctk.CTkLabel(safety_sec, text="Safety & anti-detection pacing", font=ctk.CTkFont(weight="bold", size=14)).pack(anchor="w", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            safety_sec,
            text="💡 Tip: If you are not sure what you are doing, do not modify these values. The default timings are carefully tuned to prevent rate limits and bot challenges.",
            font=ctk.CTkFont(size=11),
            text_color=theme.TEXT_MUTED
        ).pack(anchor="w", padx=12, pady=(0, 6))

        s_grid = ctk.CTkFrame(safety_sec, fg_color="transparent")
        s_grid.pack(anchor="w", padx=12, pady=6)

        ctk.CTkLabel(s_grid, text="Min delay (sec):").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        self.entry_min_delay = ctk.CTkEntry(s_grid, width=80)
        self.entry_min_delay.insert(0, str(self.config.safety.min_delay_seconds))
        self.entry_min_delay.grid(row=0, column=1, padx=6, pady=4)

        ctk.CTkLabel(s_grid, text="Max delay (sec):").grid(row=0, column=2, padx=12, pady=4, sticky="w")
        self.entry_max_delay = ctk.CTkEntry(s_grid, width=80)
        self.entry_max_delay.insert(0, str(self.config.safety.max_delay_seconds))
        self.entry_max_delay.grid(row=0, column=3, padx=6, pady=4)

        ctk.CTkLabel(s_grid, text="Breather interval (every N caches):").grid(row=1, column=0, padx=6, pady=4, sticky="w")
        self.entry_breather_interval = ctk.CTkEntry(s_grid, width=80)
        self.entry_breather_interval.insert(0, str(self.config.safety.breather_interval))
        self.entry_breather_interval.grid(row=1, column=1, padx=6, pady=4)

        ctk.CTkLabel(s_grid, text="Breather duration (sec):").grid(row=1, column=2, padx=12, pady=4, sticky="w")
        self.entry_breather_dur = ctk.CTkEntry(s_grid, width=80)
        self.entry_breather_dur.insert(0, str(self.config.safety.breather_duration_seconds))
        self.entry_breather_dur.grid(row=1, column=3, padx=6, pady=4)

        ctk.CTkButton(
            safety_sec,
            text="Set defaults",
            width=110,
            height=28,
            fg_color=theme.ACCENT_BLUE,
            command=self._on_set_safety_defaults
        ).pack(anchor="w", padx=18, pady=(4, 12))

        # Save Configuration Button
        ctk.CTkButton(
            scroll,
            text="Save configuration",
            font=ctk.CTkFont(weight="bold", size=14),
            fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            height=40,
            command=self._save_settings
        ).pack(anchor="w", padx=12, pady=14)

    def _refresh_accounts_view(self):
        for widget in self.acc_display_frame.winfo_children():
            widget.destroy()

        if not self.config.accounts:
            ctk.CTkLabel(self.acc_display_frame, text="No accounts registered. Add one below.", text_color=theme.TEXT_MUTED).pack(anchor="w", pady=4)
            return

        for acc in self.config.accounts:
            row = ctk.CTkFrame(self.acc_display_frame, fg_color=theme.CARD_BG)
            row.pack(fill="x", pady=2)

            def_tag = " (DEFAULT)" if acc.is_default else ""
            pwd_tag = "" if acc.password else " [Copy only / no pwd]"
            lbl = ctk.CTkLabel(row, text=f"#{acc.id}: {acc.username}{pwd_tag}{def_tag}", font=ctk.CTkFont(weight="bold"))
            lbl.pack(side="left", padx=10, pady=6)

            del_btn = ctk.CTkButton(
                row,
                text="Delete",
                width=65,
                height=26,
                fg_color=theme.ACCENT_RED,
                command=lambda a_id=acc.id: self._on_delete_account(a_id)
            )
            del_btn.pack(side="right", padx=6)

            if not acc.is_default:
                set_def_btn = ctk.CTkButton(
                    row,
                    text="Set default",
                    width=85,
                    height=26,
                    command=lambda a_id=acc.id: self._on_set_default_account(a_id)
                )
                set_def_btn.pack(side="right", padx=4)

    # --------------------------------------------------------------------------
    # Bottom Panel: Progress & Real-time Console Log
    # --------------------------------------------------------------------------

    def _build_bottom_panel(self):
        # Progress status line
        prog_info_frame = ctk.CTkFrame(self.bottom_frame, fg_color="transparent")
        prog_info_frame.pack(fill="x", padx=10, pady=(6, 2))

        self.lbl_progress_text = ctk.CTkLabel(prog_info_frame, text="Ready", font=ctk.CTkFont(weight="bold"))
        self.lbl_progress_text.pack(side="left")

        self.lbl_eta = ctk.CTkLabel(prog_info_frame, text="ETA: --:--", text_color=theme.TEXT_MUTED)
        self.lbl_eta.pack(side="right")

        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(self.bottom_frame, height=12)
        self.progress_bar.pack(fill="x", padx=10, pady=4)
        self.progress_bar.set(0)

        # Log Header
        log_header = ctk.CTkFrame(self.bottom_frame, fg_color="transparent")
        log_header.pack(fill="x", padx=10, pady=(4, 0))

        ctk.CTkLabel(log_header, text="Live output console:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        ctk.CTkButton(log_header, text="Clear", width=55, height=20, command=self._clear_console).pack(side="right", padx=4)
        ctk.CTkButton(log_header, text="Export log", width=75, height=20, command=self._export_console).pack(side="right", padx=4)

        # Text Console
        self.txt_console = ctk.CTkTextbox(self.bottom_frame, height=125, font=("Courier", 12))
        self.txt_console.pack(fill="both", expand=True, padx=10, pady=(4, 8))

    # --------------------------------------------------------------------------
    # Logging & Console Helpers
    # --------------------------------------------------------------------------

    def log(self, message: str, level: str = "info"):
        """Thread-safe logging into console with timestamps."""
        def append():
            timestamp = datetime.now().strftime("%H:%M:%S")
            line = f"[{timestamp}] {message}\n"
            self.txt_console.insert("end", line)
            self.txt_console.see("end")

        self.root.after(0, append)

    def _clear_console(self):
        self.txt_console.delete("1.0", "end")

    def _export_console(self):
        content = self.txt_console.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("Export", "Log is currently empty.")
            return
        filename = f"log_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=filename)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Export", f"Log successfully exported to:\n{path}")

    # --------------------------------------------------------------------------
    # Client Session Management
    # --------------------------------------------------------------------------

    def _init_client_session(self):
        """Asynchronously initialize client and test connection."""
        def worker():
            try:
                self.client = GeocachingClient()
                default_acc = self.config_mgr.get_default_account()
                target_user = default_acc.username if default_acc else None

                if target_user:
                    success, msg = self.client.switch_account(target_user, default_acc.password if default_acc else None)
                    if success:
                        self.root.after(0, lambda: self._update_status_badge(True, f"● Connected as {target_user}"))
                        self.root.after(0, self._refresh_copy_account_combo)
                        self.log(f"Session active for user '{target_user}'.", "success")
                    elif default_acc and default_acc.password:
                        self.root.after(0, lambda: self._update_status_badge(False, "● Not connected"))
                        self.log(f"Auto-login failed for '{target_user}': {msg}", "warning")
                    else:
                        self.root.after(0, lambda: self._update_status_badge(False, "● Not connected"))
                        self.log(f"Account '{target_user}' has no session and no password configured.", "warning")
                else:
                    if self.client.is_logged_in():
                        user = self.client.logged_in_username or "User"
                        self.root.after(0, lambda: self._update_status_badge(True, f"● Connected as {user}"))
                        self.root.after(0, self._refresh_copy_account_combo)
                        self.log(f"Session restored from saved cookies for user '{user}'.", "success")
                    else:
                        self.root.after(0, lambda: self._update_status_badge(False, "● No account"))
            except Exception as e:
                self.root.after(0, lambda: self._update_status_badge(False, "● Error"))
                self.log(f"Client init error: {e}", "error")

        threading.Thread(target=worker, daemon=True).start()

    def _update_status_badge(self, connected: Optional[bool], text: str):
        if connected is True:
            self.status_badge.configure(text=text, text_color=theme.ACCENT_GREEN)
        elif connected is False:
            self.status_badge.configure(text=text, text_color=theme.ACCENT_RED)
        else:
            self.status_badge.configure(text=text, text_color=theme.ACCENT_YELLOW)

    def _on_click_login(self):
        """Manual login / re-auth trigger."""
        current_user = self.acc_combo.get()
        acc = self.config_mgr.get_account_by_username(current_user)
        if not acc:
            messagebox.showwarning("Login", "Please select or add an account first.")
            return
        if not acc.password:
            messagebox.showwarning("Login", f"Account '{acc.username}' has no password configured. Passwordless accounts can only be used as copy targets.")
            return

        def worker():
            self.root.after(0, lambda: self._update_status_badge(None, "● Authenticating..."))
            self.log(f"Logging in as {acc.username}...", "info")
            success, msg = self.client.login(acc.username, acc.password)
            if success:
                self.root.after(0, lambda: self._update_status_badge(True, f"● Connected as {acc.username}"))
                self.log(f"Login successful: {msg}", "success")
            else:
                self.root.after(0, lambda: self._update_status_badge(False, "● Login failed"))
                self.log(f"Login failed: {msg}", "error")
                messagebox.showerror("Login failed", msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_account_switched(self, choice: str):
        acc = self.config_mgr.get_account_by_username(choice)
        if not acc:
            return
        self.log(f"Switched account to '{acc.username}'.", "info")
        self._refresh_copy_account_combo()

        def worker():
            self.root.after(0, lambda: self._update_status_badge(None, f"● Connecting as {acc.username}..."))
            success, msg = self.client.switch_account(acc.username, acc.password)
            if success:
                self.root.after(0, lambda: self._update_status_badge(True, f"● Connected as {acc.username}"))
                self.log(f"Connected as '{acc.username}': {msg}", "success")
            else:
                self.root.after(0, lambda: self._update_status_badge(False, "● Not connected"))
                self.log(f"Could not connect as '{acc.username}': {msg}", "error" if acc.password else "info")
                if acc.password:
                    messagebox.showerror("Login failed", f"Could not authenticate as '{acc.username}':\n\n{msg}")

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Handlers: Coordinates Upload
    # --------------------------------------------------------------------------

    def _browse_coords_file(self):
        f = filedialog.askopenfilename(filetypes=[("GPX & LOC files", "*.gpx *.loc"), ("All files", "*.*")])
        if f:
            self.coords_path_entry.delete(0, "end")
            self.coords_path_entry.insert(0, f)
            self._load_coords_items()

    def _browse_coords_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.coords_path_entry.delete(0, "end")
            self.coords_path_entry.insert(0, d)
            self._load_coords_items()

    def _load_coords_from_path(self, path: str):
        if not path:
            return
        if os.path.isfile(path):
            self.loaded_waypoints = parse_file(path)
        elif os.path.isdir(path):
            self.loaded_waypoints = parse_folder(path)
        else:
            self.lbl_coords_summary.configure(text=f"Path not found: {path}", text_color=theme.ACCENT_RED)
            return

        count = len(self.loaded_waypoints)
        modified_hints = sum(1 for w in self.loaded_waypoints if w.is_modified_hint)
        self.lbl_coords_summary.configure(
            text=f"Loaded {count} caches ({modified_hints} flagged with modified coords in GPX).",
            text_color=theme.ACCENT_GREEN if count > 0 else theme.TEXT_MUTED
        )
        self.log(f"Loaded {count} waypoints from '{os.path.basename(path)}'.", "info")

    def _load_coords_items(self):
        path = self.coords_path_entry.get().strip()
        self._load_coords_from_path(path)

    def _check_active_checkpoint(self):
        if not hasattr(self, "frame_resume_coords"):
            return
        if self.checkpoint_mgr.has_active_coords_run() and not self.is_running:
            state = self.checkpoint_mgr.load_active_coords_run()
            if state:
                fname = os.path.basename(state.file_path) or "GPX batch"
                done = len(state.completed_gccodes)
                self.lbl_resume_info.configure(
                    text=f"⚠️ Unfinished upload detected: '{fname}' ({done}/{state.total} caches processed)."
                )
                self.frame_resume_coords.pack(fill="x", pady=6, padx=6, before=self.coords_opt_frame)
        else:
            self.frame_resume_coords.pack_forget()

    def _on_resume_coords_clicked(self):
        state = self.checkpoint_mgr.load_active_coords_run()
        if not state:
            self.frame_resume_coords.pack_forget()
            return

        if state.file_path and os.path.exists(state.file_path):
            self.coords_path_entry.delete(0, "end")
            self.coords_path_entry.insert(0, state.file_path)
            self._load_coords_from_path(state.file_path)
        elif not self.loaded_waypoints:
            messagebox.showwarning("Resume upload", f"Source file was moved or deleted:\n{state.file_path}\nPlease re-select the file.")
            return

        self.coords_mode_var.set(state.mode)
        saved_logs = self.checkpoint_mgr.read_saved_logs(25)
        if saved_logs:
            self.log("--- Restoring recent log from previous interrupted run ---", "info")
            for line in saved_logs:
                self.txt_console.insert("end", f"{line}\n")
            self.txt_console.see("end")

        done_count = len(state.completed_gccodes)
        self.log(f"Resuming upload from checkpoint: {done_count}/{state.total} caches already completed.", "info")
        self._start_coords_upload(resume=True)

    def _on_discard_coords_clicked(self):
        if messagebox.askyesno("Discard checkpoint", "Discard progress from the unfinished upload?"):
            self.checkpoint_mgr.discard_coords_run()
            self.frame_resume_coords.pack_forget()
            self.log("Discarded unfinished upload checkpoint.", "info")

    def _start_coords_upload(self, resume: bool = False):
        if not self.loaded_waypoints:
            messagebox.showwarning("Upload", "No waypoints loaded. Choose a GPX file first.")
            return
        if self.is_running:
            return

        self.is_running = True
        self.btn_coords_start.configure(state="disabled")
        self.btn_coords_pause.configure(state="normal", text="⏸ Pause")
        self.btn_coords_stop.configure(state="normal")
        self.progress_bar.set(0)

        if hasattr(self, "frame_resume_coords"):
            self.frame_resume_coords.pack_forget()

        mode = self.coords_mode_var.get()
        source_path = self.coords_path_entry.get().strip()
        uploader = CoordinateUploader(self.client, self.safety, checkpoint_manager=self.checkpoint_mgr)

        def on_progress(idx, total, pct, eta, item):
            self.root.after(0, lambda: self._update_progress_ui(idx, total, pct, eta, f"Uploading {item.gccode}"))

        def worker():
            try:
                res = uploader.run(
                    self.loaded_waypoints,
                    mode=mode,
                    on_progress=on_progress,
                    on_log=self.log,
                    source_file_path=source_path,
                    resume=resume
                )
                self.root.after(0, lambda: self._on_operation_done("Coordinates upload", res))
            finally:
                self.root.after(0, self._reset_controls)
                self.root.after(0, self._check_active_checkpoint)

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Handlers: Bulk Logging
    # --------------------------------------------------------------------------

    def _on_template_selected(self, val: str):
        self.txt_log_message.delete("1.0", "end")
        self.txt_log_message.insert("1.0", val)

    def _import_codes_from_file(self):
        f = filedialog.askopenfilename(filetypes=[("GPX & LOC files", "*.gpx *.loc"), ("Text files", "*.txt"), ("All files", "*.*")])
        if f:
            items = parse_file(f)
            codes = [it.gccode for it in items]
            if codes:
                self.txt_gc_codes.insert("end", " ".join(codes) + "\n")
                self.log(f"Imported {len(codes)} GC codes from {os.path.basename(f)}.", "info")

    def _start_bulk_logging(self):
        raw_text = self.txt_gc_codes.get("1.0", "end").strip()
        codes = extract_gc_codes_from_string(raw_text)
        if not codes:
            messagebox.showwarning("Log caches", "No valid GC codes found. Please paste GC codes or import a GPX.")
            return
        if self.is_running:
            return

        # Find internal log type key
        display_name = self.combo_log_type.get()
        type_key = "FOUND"
        for k, v in LOG_TYPE_NAMES.items():
            if v == display_name:
                type_key = k
                break

        if type_key == "NEEDS_REVIEWER_ATTENTION":
            if not messagebox.askyesno("Warning", "You selected 'Needs archive'. This alerts reviewers to ALL listed caches. Continue?"):
                return

        date_str = self._parse_to_iso_date(self.entry_date.get())
        log_text = self.txt_log_message.get("1.0", "end").strip()

        self.is_running = True
        self.btn_log_start.configure(state="disabled")
        self.progress_bar.set(0)

        logger = CacheLogger(self.client, self.safety)

        def on_progress(idx, total, pct, eta, gc_code):
            self.root.after(0, lambda: self._update_progress_ui(idx, total, pct, eta, f"Logging {gc_code}"))

        def worker():
            try:
                res = logger.run_log(
                    codes,
                    log_type_key=type_key,
                    date_str=date_str,
                    log_text=log_text,
                    on_progress=on_progress,
                    on_log=self.log
                )
                self.root.after(0, lambda: self._on_operation_done("Bulk logging", res))
            finally:
                self.root.after(0, self._reset_controls)

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Handlers: Copy User & Ignore
    # --------------------------------------------------------------------------

    def _start_copy_user_fetch(self):
        target_user = self.entry_copy_user.get().strip()
        target_pwd = self.entry_copy_pwd.get().strip() if self.var_password_mode.get() else None
        target_date = self._parse_to_iso_date(self.entry_copy_date.get())

        if not target_user:
            messagebox.showwarning("Copy user", "Enter a target username.")
            return

        if self.var_password_mode.get() and not target_pwd:
            messagebox.showwarning("Copy user", "Password mode is enabled, but no password was entered.\nPlease enter a password or uncheck Password mode.")
            return

        def worker():
            copier = UserLogCopier(self.client, self.safety)
            codes = copier.fetch_user_logged_caches(target_user, target_pwd, target_date, on_log=self.log)
            if codes:
                self.root.after(0, lambda: self._insert_copied_codes(codes))
            else:
                self.log(f"No caches found for '{target_user}' on {target_date}.", "warning")

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_password_mode(self):
        if self.var_password_mode.get():
            self.lbl_copy_pwd.grid(row=3, column=0, padx=6, pady=6, sticky="w")
            self.entry_copy_pwd.grid(row=3, column=1, padx=6, pady=6, sticky="w")
            chosen = self.combo_copy_account.get()
            acc = self.config_mgr.get_account_by_username(chosen)
            if acc and acc.password:
                self.entry_copy_pwd.delete(0, "end")
                self.entry_copy_pwd.insert(0, acc.password)
        else:
            self.lbl_copy_pwd.grid_remove()
            self.entry_copy_pwd.grid_remove()
            self.entry_copy_pwd.delete(0, "end")

    def _on_copy_account_selected(self, choice: str):
        """Automatically fill credentials when an account is selected from the dropdown."""
        acc = self.config_mgr.get_account_by_username(choice)
        if acc:
            self.entry_copy_user.delete(0, "end")
            self.entry_copy_user.insert(0, acc.username)
            if self.var_password_mode.get():
                self.entry_copy_pwd.delete(0, "end")
                self.entry_copy_pwd.insert(0, acc.password or "")
        elif choice == "[Custom user...]":
            self.entry_copy_user.delete(0, "end")
            self.entry_copy_pwd.delete(0, "end")

    def _browse_ignore_file(self):
        f = filedialog.askopenfilename(filetypes=[("GPX & LOC files", "*.gpx *.loc"), ("All files", "*.*")])
        if f:
            self.ignore_path_entry.delete(0, "end")
            self.ignore_path_entry.insert(0, f)
            self._import_ignore_file()

    def _browse_ignore_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.ignore_path_entry.delete(0, "end")
            self.ignore_path_entry.insert(0, d)
            self._import_ignore_file()

    def _import_ignore_file(self):
        path = self.ignore_path_entry.get().strip()
        if not path:
            return
        codes = []
        if os.path.isfile(path):
            items = parse_file(path)
            codes = [it.gccode for it in items]
        elif os.path.isdir(path):
            items = parse_folder(path)
            codes = [it.gccode for it in items]
        else:
            messagebox.showwarning("Import", f"Path not found: {path}")
            return

        if codes:
            current = self.txt_ignore_codes.get("1.0", "end").strip()
            new_text = (current + "\n" if current else "") + "\n".join(codes) + "\n"
            self.txt_ignore_codes.delete("1.0", "end")
            self.txt_ignore_codes.insert("1.0", new_text)
            self.log(f"Imported {len(codes)} GC codes from '{os.path.basename(path)}' into Ignore List.", "info")
            messagebox.showinfo("Imported", f"Successfully imported {len(codes)} GC codes!")
        else:
            messagebox.showinfo("Import", "No GC codes found in given source.")

    def _insert_copied_codes(self, codes: List[str]):
        self.tabview.set("📝 Bulk log caches")
        self.txt_gc_codes.delete("1.0", "end")
        self.txt_gc_codes.insert("1.0", " ".join(codes))
        messagebox.showinfo("Caches loaded", f"Loaded {len(codes)} caches into Bulk Logging tab!")

    def _start_bulk_ignore(self):
        raw = self.txt_ignore_codes.get("1.0", "end").strip()
        codes = extract_gc_codes_from_string(raw)
        if not codes:
            messagebox.showwarning("Ignore", "No GC codes found to ignore.")
            return

        if not messagebox.askyesno("Confirm", f"Add {len(codes)} caches to Ignore list?"):
            return

        self.is_running = True
        logger = CacheLogger(self.client, self.safety)

        def worker():
            try:
                res = logger.run_ignore(codes, on_log=self.log)
                self.root.after(0, lambda: self._on_operation_done("Ignore list", res))
            finally:
                self.root.after(0, self._reset_controls)

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Settings / Config Tab Handlers
    # --------------------------------------------------------------------------

    def _on_add_account(self):
        u = self.new_acc_user.get().strip()
        p = self.new_acc_pwd.get().strip()
        d = bool(self.new_acc_default.get())
        if not u:
            messagebox.showwarning("Account", "Username is required.")
            return
        self.config_mgr.add_or_update_account(u, p, is_default=d)
        self.new_acc_user.delete(0, "end")
        self.new_acc_pwd.delete(0, "end")
        self.new_acc_default.deselect()
        self._refresh_accounts_view()
        self._refresh_header_combo()
        self._refresh_copy_account_combo()
        self.log(f"Account '{u}' added to config.", "success")

    def _on_delete_account(self, acc_id: str):
        if messagebox.askyesno("Delete account", "Remove this account from configuration?"):
            acc = self.config_mgr.get_account_by_id(acc_id)
            if acc and hasattr(self, "client"):
                self.client.clear_cookies(acc.username)
            self.config_mgr.remove_account(acc_id)
            self._refresh_accounts_view()
            self._refresh_header_combo()
            self._refresh_copy_account_combo()

    def _on_set_default_account(self, acc_id: str):
        for a in self.config.accounts:
            a.is_default = (a.id == acc_id)
        self.config_mgr.save()
        self._refresh_accounts_view()
        self._refresh_header_combo()
        self._refresh_copy_account_combo()

    def _refresh_header_combo(self):
        accs_with_pwd = [a for a in self.config.accounts if a.password]
        if accs_with_pwd:
            names = [a.username for a in accs_with_pwd]
        else:
            names = [a.username for a in self.config.accounts] or ["No accounts"]
        cur = self.acc_combo.get() if hasattr(self, "acc_combo") else ""
        self.acc_combo.configure(values=names)
        if cur in names:
            self.acc_combo.set(cur)
        else:
            def_acc = self.config_mgr.get_default_account()
            if def_acc and def_acc.username in names:
                self.acc_combo.set(def_acc.username)
            elif names:
                self.acc_combo.set(names[0])
        self._refresh_copy_account_combo()

    def _refresh_copy_account_combo(self):
        active_user = self.acc_combo.get().strip() if hasattr(self, "acc_combo") else ""
        other_accounts = [
            "[Custom user...]"
        ] + [
            a.username for a in self.config.accounts
            if a.username != active_user
        ]
        current_val = self.combo_copy_account.get()
        self.combo_copy_account.configure(values=other_accounts)
        if current_val in other_accounts and current_val != "[Custom user...]":
            self.combo_copy_account.set(current_val)
        else:
            self.combo_copy_account.set("[Custom user...]")

    def _refresh_templates_view(self):
        for widget in self.tmpl_display_frame.winfo_children():
            widget.destroy()

        if not self.config.log_templates:
            ctk.CTkLabel(self.tmpl_display_frame, text="No templates configured. Add one below.", text_color=theme.TEXT_MUTED).pack(anchor="w", pady=4)
            return

        for idx, tmpl in enumerate(self.config.log_templates, 1):
            row = ctk.CTkFrame(self.tmpl_display_frame, fg_color=theme.CARD_BG)
            row.pack(fill="x", pady=2)

            display_snip = tmpl.replace("\n", " ").strip()
            if len(display_snip) > 65:
                display_snip = display_snip[:62] + "..."

            lbl = ctk.CTkLabel(row, text=f"#{idx}: {display_snip}", font=ctk.CTkFont(weight="bold"))
            lbl.pack(side="left", padx=10, pady=6)

            del_btn = ctk.CTkButton(
                row,
                text="Delete",
                width=65,
                height=26,
                fg_color=theme.ACCENT_RED,
                command=lambda t_idx=idx-1: self._on_delete_template(t_idx)
            )
            del_btn.pack(side="right", padx=6)

    def _on_add_template(self):
        t = self.new_tmpl_entry.get().strip()
        if not t:
            messagebox.showwarning("Log template", "Template text cannot be empty.")
            return
        if t in self.config.log_templates:
            messagebox.showwarning("Log template", "This template already exists.")
            return
        self.config.log_templates.append(t)
        self.config_mgr.save()
        self.new_tmpl_entry.delete(0, "end")
        self._refresh_templates_view()
        self.combo_template.configure(values=self.config.log_templates)
        self.log(f"Added log template #{len(self.config.log_templates)}.", "success")

    def _on_delete_template(self, index: int):
        if 0 <= index < len(self.config.log_templates):
            tmpl_text = self.config.log_templates[index]
            display_snip = (tmpl_text[:40] + "...") if len(tmpl_text) > 40 else tmpl_text
            if messagebox.askyesno("Delete template", f"Remove template #{index + 1}?\n\"{display_snip}\""):
                self.config.log_templates.pop(index)
                if not self.config.log_templates:
                    self.config.log_templates = ["Thanks for the cache!"]
                self.config_mgr.save()
                self._refresh_templates_view()
                self.combo_template.configure(values=self.config.log_templates)
                self.log(f"Removed template #{index + 1}.", "info")

    def _on_set_safety_defaults(self):
        self.entry_min_delay.delete(0, "end")
        self.entry_min_delay.insert(0, "0.7")
        self.entry_max_delay.delete(0, "end")
        self.entry_max_delay.insert(0, "1.2")
        self.entry_breather_interval.delete(0, "end")
        self.entry_breather_interval.insert(0, "50")
        self.entry_breather_dur.delete(0, "end")
        self.entry_breather_dur.insert(0, "4.0")
        self.log("Restored default safety pacing values.", "info")

    def _save_settings(self):
        try:
            self.config.safety.min_delay_seconds = float(self.entry_min_delay.get().strip())
            self.config.safety.max_delay_seconds = float(self.entry_max_delay.get().strip())
            self.config.safety.breather_interval = int(self.entry_breather_interval.get().strip())
            self.config.safety.breather_duration_seconds = float(self.entry_breather_dur.get().strip())

            # Update active safety manager
            self.safety.min_delay = self.config.safety.min_delay_seconds
            self.safety.max_delay = self.config.safety.max_delay_seconds
            self.safety.breather_interval = self.config.safety.breather_interval
            self.safety.breather_duration = self.config.safety.breather_duration_seconds

            if self.config_mgr.save():
                messagebox.showinfo("Saved", "Settings successfully saved to config.json!")
                self.log("Configuration updated and saved.", "success")
            else:
                messagebox.showerror("Error", "Failed to save config.json")
        except ValueError as e:
            messagebox.showerror("Validation error", f"Invalid number format: {e}")

    @staticmethod
    def _parse_to_iso_date(text: str) -> str:
        clean = text.strip()
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(clean, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
        return clean

    @staticmethod
    def _set_date_today(entry: ctk.CTkEntry):
        entry.delete(0, "end")
        entry.insert(0, datetime.now().strftime("%d-%m-%Y"))

    @staticmethod
    def _step_date(entry: ctk.CTkEntry, delta_days: int):
        clean = entry.get().strip()
        d = None
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                d = datetime.strptime(clean, fmt).date()
                break
            except ValueError:
                pass
        if not d:
            d = datetime.now().date()

        new_d = d + timedelta(days=delta_days)
        today = datetime.now().date()
        if new_d > today:
            new_d = today
        entry.delete(0, "end")
        entry.insert(0, new_d.strftime("%d-%m-%Y"))

    # --------------------------------------------------------------------------
    # Progress & Operation Controls
    # --------------------------------------------------------------------------

    def _toggle_pause(self):
        if self.safety.is_paused:
            self.safety.resume()
            self.btn_coords_pause.configure(text="⏸ Pause")
            self.log("Operation resumed.", "info")
        else:
            self.safety.pause()
            self.btn_coords_pause.configure(text="▶ Resume")
            self.log("Operation paused by user.", "warning")

    def _stop_operation(self):
        if messagebox.askyesno("Stop", "Stop the current batch operation?"):
            self.safety.cancel()
            self.log("Cancelling operation...", "warning")

    def _update_progress_ui(self, current: int, total: int, percent: float, eta_seconds: Optional[float], text: str):
        self.progress_bar.set(percent / 100.0)
        self.lbl_progress_text.configure(text=f"[{current}/{total}] ({percent:.1f}%) - {text}")
        if eta_seconds is not None:
            m, s = divmod(int(eta_seconds), 60)
            self.lbl_eta.configure(text=f"ETA: {m:02d}m {s:02d}s")

    def _on_operation_done(self, title: str, res: Dict[str, Any]):
        msg = f"{title} finished!\n\nSuccess: {res.get('success', 0)}\nFailed: {res.get('failed', 0)}"
        if "skipped" in res:
            msg += f"\nSkipped (Already modified): {res.get('skipped', 0)}"
        msg += f"\nElapsed: {res.get('elapsed_seconds', 0):.1f}s"
        messagebox.showinfo("Operation complete", msg)

    def _reset_controls(self):
        self.is_running = False
        self.btn_coords_start.configure(state="normal")
        self.btn_coords_pause.configure(state="disabled", text="⏸ Pause")
        self.btn_coords_stop.configure(state="disabled")
        self.btn_log_start.configure(state="normal")
        self.lbl_progress_text.configure(text="Finished")
        self.lbl_eta.configure(text="ETA: --:--")

    def run(self):
        """Start the GUI main loop."""
        self.root.mainloop()


if __name__ == "__main__":
    app = UltimateApp()
    app.run()

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

from core.config import ConfigManager, AccountConfig, get_base_dir, get_bundle_dir
from core.client import GeocachingClient
from core.safety import SafetyManager
from core.checkpoint import RunCheckpointManager
from core.gpx_parser import parse_file, parse_folder, extract_gc_codes_from_string, WaypointItem, is_virtual_or_earth
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

        # Console log buffering for high UI responsiveness
        self._log_buffer: List[str] = []
        self._log_lock = threading.Lock()
        self._log_flush_pending = False

        # Drag-and-drop state
        self._drag_acc_idx: Optional[int] = None
        self._acc_rows: List[tuple] = []
        self._drag_tmpl_idx: Optional[int] = None
        self._tmpl_rows: List[tuple] = []

        self._init_ui()
        self._set_app_icon()
        self._init_client_session()
        self._check_active_checkpoint()

    def _set_app_icon(self):
        """Set window icon and macOS Dock icon if assets exist."""
        bundle_dir = get_bundle_dir()
        base_dir = get_base_dir()

        png_path = ""
        ico_path = ""
        icns_path = ""

        for d in [bundle_dir, base_dir]:
            p = os.path.join(d, "assets", "icon.png")
            i = os.path.join(d, "assets", "icon.ico")
            c = os.path.join(d, "assets", "icon.icns")
            if os.path.exists(p) or os.path.exists(i) or os.path.exists(c):
                png_path = p
                ico_path = i
                icns_path = c
                break

        # 1. macOS Dock icon via AppKit / Cocoa (prefers native multi-res .icns)
        if sys.platform == "darwin":
            icon_file = icns_path if (icns_path and os.path.exists(icns_path)) else png_path
            if icon_file and os.path.exists(icon_file):
                try:
                    from AppKit import NSApplication, NSImage
                    app = NSApplication.sharedApplication()
                    icon = NSImage.alloc().initWithContentsOfFile_(icon_file)
                    if icon:
                        app.setApplicationIconImage_(icon)
                except Exception:
                    pass

        # 2. Window titlebar & taskbar icon (Windows / Linux)
        if ico_path and os.path.exists(ico_path) and sys.platform == "win32":
            try:
                self.root.iconbitmap(ico_path)
            except Exception:
                pass
        elif png_path and os.path.exists(png_path):
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
        # Action mode selector (Create logs / Edit logs / Delete logs)
        mode_frame = ctk.CTkFrame(self.tab_logger, fg_color="transparent")
        mode_frame.pack(fill="x", padx=14, pady=(8, 4))

        ctk.CTkLabel(mode_frame, text="Action:", font=ctk.CTkFont(weight="bold", size=13)).pack(side="left", padx=(0, 10))

        self.logger_mode_var = ctk.StringVar(value="CREATE")
        self.logger_mode_seg = ctk.CTkSegmentedButton(
            mode_frame,
            values=["Create logs", "Edit logs", "Delete logs"],
            command=self._on_logger_mode_changed
        )
        self.logger_mode_seg.set("Create logs")
        self.logger_mode_seg.pack(side="left")

        log_grid = ctk.CTkFrame(self.tab_logger, fg_color="transparent")
        log_grid.pack(fill="both", expand=True, padx=8, pady=4)

        # Left side: inputs
        self.logger_left_box = ctk.CTkFrame(log_grid)
        self.logger_left_box.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        # Delete mode informational frame (shown only in Delete mode)
        self.frame_delete_info = ctk.CTkFrame(
            self.logger_left_box,
            fg_color="#3b1d1d",
            border_width=1,
            border_color="#ef4444",
            corner_radius=6
        )
        ctk.CTkLabel(
            self.frame_delete_info,
            text="🗑️ Delete Mode:\nPermanently deletes your existing logs on Geocaching.com\nfor all listed GC codes.\n\nNo date, type, or log text input required.",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#fca5a5",
            justify="left"
        ).pack(padx=16, pady=20)

        # Log Type
        self.lbl_log_type = ctk.CTkLabel(self.logger_left_box, text="Log type:", font=ctk.CTkFont(weight="bold"))
        self.lbl_log_type.grid(row=0, column=0, padx=10, pady=8, sticky="w")
        type_options = list(LOG_TYPE_NAMES.values())
        self.combo_log_type = ctk.CTkComboBox(self.logger_left_box, values=type_options, width=200)
        self.combo_log_type.set("Found it")
        self.combo_log_type.grid(row=0, column=1, padx=10, pady=8, sticky="w")

        # Date
        self.lbl_log_date = ctk.CTkLabel(self.logger_left_box, text="Log date (DD-MM-YYYY):", font=ctk.CTkFont(weight="bold"))
        self.lbl_log_date.grid(row=1, column=0, padx=10, pady=8, sticky="w")
        self.date_box = ctk.CTkFrame(self.logger_left_box, fg_color="transparent")
        self.date_box.grid(row=1, column=1, padx=10, pady=8, sticky="w")
        self.entry_date = ctk.CTkEntry(self.date_box, width=120)
        self.entry_date.insert(0, datetime.now().strftime("%d-%m-%Y"))
        self.entry_date.pack(side="left")
        ctk.CTkButton(self.date_box, text="Today", width=55, command=lambda: self._set_date_today(self.entry_date)).pack(side="left", padx=3)
        ctk.CTkButton(self.date_box, text="▼", width=28, command=lambda: self._step_date(self.entry_date, -1)).pack(side="left", padx=2)
        ctk.CTkButton(self.date_box, text="▲", width=28, command=lambda: self._step_date(self.entry_date, 1)).pack(side="left", padx=2)

        # Templates
        self.lbl_log_tmpl = ctk.CTkLabel(self.logger_left_box, text="Template:", font=ctk.CTkFont(weight="bold"))
        self.lbl_log_tmpl.grid(row=2, column=0, padx=10, pady=8, sticky="w")
        templates = self.config.log_templates or ["TFTC."]
        self.combo_template = ctk.CTkComboBox(self.logger_left_box, values=templates, width=320, command=self._on_template_selected)
        self.combo_template.grid(row=2, column=1, padx=10, pady=8, sticky="w")

        # Log Text Box (with wrap="word" so words remain whole)
        self.lbl_log_msg = ctk.CTkLabel(self.logger_left_box, text="Log text:", font=ctk.CTkFont(weight="bold"))
        self.lbl_log_msg.grid(row=3, column=0, padx=10, pady=4, sticky="nw")
        self.txt_log_message = ctk.CTkTextbox(self.logger_left_box, height=120, wrap="word")
        self.txt_log_message.grid(row=3, column=1, padx=10, pady=4, sticky="nsew")
        if templates:
            self.txt_log_message.insert("1.0", templates[0])

        # Right side: Target GC codes (with wrap="word")
        right_box = ctk.CTkFrame(log_grid)
        right_box.pack(side="right", fill="both", expand=True, padx=5, pady=5)

        ctk.CTkLabel(right_box, text="Target GC codes (from GPX or paste here):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=6)
        self.txt_gc_codes = ctk.CTkTextbox(right_box, height=170, wrap="word")
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

    def _on_logger_mode_changed(self, mode_val: str):
        if mode_val == "Create logs":
            self.logger_mode_var.set("CREATE")
            self.frame_delete_info.grid_remove()
            self.lbl_log_type.grid(row=0, column=0, padx=10, pady=8, sticky="w")
            self.combo_log_type.grid(row=0, column=1, padx=10, pady=8, sticky="w")
            self.lbl_log_date.grid(row=1, column=0, padx=10, pady=8, sticky="w")
            self.date_box.grid(row=1, column=1, padx=10, pady=8, sticky="w")
            self.lbl_log_tmpl.grid(row=2, column=0, padx=10, pady=8, sticky="w")
            self.combo_template.grid(row=2, column=1, padx=10, pady=8, sticky="w")
            self.lbl_log_msg.grid(row=3, column=0, padx=10, pady=4, sticky="nw")
            self.txt_log_message.grid(row=3, column=1, padx=10, pady=4, sticky="nsew")

            type_options = list(LOG_TYPE_NAMES.values())
            self.combo_log_type.configure(values=type_options)
            if self.combo_log_type.get() not in type_options:
                self.combo_log_type.set("Found it")

            templates = self.config.log_templates or ["TFTC."]
            self.combo_template.configure(values=templates)
            self.combo_template.set(templates[0])
            self.txt_log_message.delete("1.0", "end")
            self.txt_log_message.insert("1.0", templates[0])

            self.btn_log_start.configure(
                text="▶ Start bulk logging",
                fg_color=theme.ACCENT_GREEN,
                hover_color=theme.ACCENT_GREEN_HOVER
            )

        elif mode_val == "Edit logs":
            self.logger_mode_var.set("EDIT")
            self.frame_delete_info.grid_remove()
            self.lbl_log_type.grid(row=0, column=0, padx=10, pady=8, sticky="w")
            self.combo_log_type.grid(row=0, column=1, padx=10, pady=8, sticky="w")
            self.lbl_log_date.grid(row=1, column=0, padx=10, pady=8, sticky="w")
            self.date_box.grid(row=1, column=1, padx=10, pady=8, sticky="w")
            self.lbl_log_tmpl.grid(row=2, column=0, padx=10, pady=8, sticky="w")
            self.combo_template.grid(row=2, column=1, padx=10, pady=8, sticky="w")
            self.lbl_log_msg.grid(row=3, column=0, padx=10, pady=4, sticky="nw")
            self.txt_log_message.grid(row=3, column=1, padx=10, pady=4, sticky="nsew")

            type_options = ["[Ponechat původní]"] + list(LOG_TYPE_NAMES.values())
            self.combo_log_type.configure(values=type_options)
            self.combo_log_type.set("[Ponechat původní]")

            tmpl_options = ["[Ponechat původní]"] + (self.config.log_templates or ["TFTC."])
            self.combo_template.configure(values=tmpl_options)
            self.combo_template.set("[Ponechat původní]")
            self.txt_log_message.delete("1.0", "end")
            self.txt_log_message.insert("1.0", "[Ponechat původní]")

            self.btn_log_start.configure(
                text="✏️ Start editing logs",
                fg_color=theme.ACCENT_BLUE,
                hover_color=theme.ACCENT_BLUE_HOVER
            )

        elif mode_val == "Delete logs":
            self.logger_mode_var.set("DELETE")
            self.lbl_log_type.grid_remove()
            self.combo_log_type.grid_remove()
            self.lbl_log_date.grid_remove()
            self.date_box.grid_remove()
            self.lbl_log_tmpl.grid_remove()
            self.combo_template.grid_remove()
            self.lbl_log_msg.grid_remove()
            self.txt_log_message.grid_remove()

            self.frame_delete_info.grid(row=0, column=0, columnspan=2, padx=10, pady=20, sticky="nsew")

            self.btn_log_start.configure(
                text="🗑️ Delete logs",
                fg_color=theme.ACCENT_RED,
                hover_color=theme.ACCENT_RED_HOVER
            )
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

        # Row 4: Start date (hidden unless Enter Date Range is checked)
        self.lbl_copy_start_date = ctk.CTkLabel(grid, text="Start date (DD-MM-YYYY):")
        self.start_date_box = ctk.CTkFrame(grid, fg_color="transparent")
        self.entry_copy_start_date = ctk.CTkEntry(self.start_date_box, width=120)
        self.entry_copy_start_date.pack(side="left")
        ctk.CTkButton(
            self.start_date_box,
            text="▼",
            width=28,
            command=lambda: self._step_date(self.entry_copy_start_date, -1)
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            self.start_date_box,
            text="▲",
            width=28,
            command=lambda: self._step_date(self.entry_copy_start_date, 1)
        ).pack(side="left", padx=2)

        # Row 5: Date (switches to 'End date' when range is enabled)
        self.lbl_copy_main_date = ctk.CTkLabel(grid, text="Date (DD-MM-YYYY):")
        self.lbl_copy_main_date.grid(row=5, column=0, padx=6, pady=6, sticky="w")
        date_box = ctk.CTkFrame(grid, fg_color="transparent")
        date_box.grid(row=5, column=1, padx=6, pady=6, sticky="w")
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

        self.var_date_range = ctk.BooleanVar(value=False)
        self.chk_date_range = ctk.CTkCheckBox(
            grid,
            text="Enter date range",
            variable=self.var_date_range,
            command=self._toggle_date_range
        )
        self.chk_date_range.grid(row=5, column=2, padx=14, pady=6, sticky="w")

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

        self.txt_ignore_codes = ctk.CTkTextbox(box, height=160, wrap="word")
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

        # 3. Filters Section
        filter_sec = ctk.CTkFrame(scroll)
        filter_sec.pack(fill="x", pady=6, padx=6)
        ctk.CTkLabel(filter_sec, text="Filters", font=ctk.CTkFont(weight="bold", size=14)).pack(anchor="w", padx=12, pady=(10, 4))

        filter_content = ctk.CTkFrame(filter_sec, fg_color="transparent")
        filter_content.pack(fill="x", padx=12, pady=(4, 12))

        self.var_omit_virtual_earth = ctk.BooleanVar(value=self.config.filters.omit_virtual_earth_webcam)
        self.chk_omit_virtual_earth = ctk.CTkCheckBox(
            filter_content,
            text="Omit virtual, earth, and webcam caches",
            font=ctk.CTkFont(size=13),
            variable=self.var_omit_virtual_earth,
            command=self._on_toggle_omit_virtual_earth
        )
        self.chk_omit_virtual_earth.pack(anchor="w", padx=4, pady=4)

        ctk.CTkLabel(
            filter_content,
            text="💡 Automatically skips Virtual, EarthCache, and Webcam types during coordinate uploading and bulk logging.",
            font=ctk.CTkFont(size=11),
            text_color=theme.TEXT_MUTED
        ).pack(anchor="w", padx=4, pady=(0, 4))

        # 4. Safety & Delays Section
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

        self._acc_rows = []

        if not self.config.accounts:
            ctk.CTkLabel(self.acc_display_frame, text="No accounts registered. Add one below.", text_color=theme.TEXT_MUTED).pack(anchor="w", pady=4)
            return

        total = len(self.config.accounts)
        for idx, acc in enumerate(self.config.accounts):
            row = ctk.CTkFrame(self.acc_display_frame, fg_color=theme.CARD_BG, corner_radius=6, border_width=1, border_color=theme.CARD_BORDER)
            row.pack(fill="x", pady=2)
            self._acc_rows.append((row, idx))

            # Drag handle
            drag_handle = ctk.CTkLabel(row, text=" ☰ ", font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.TEXT_MUTED, cursor="fleur")
            drag_handle.pack(side="left", padx=(8, 2))

            # Up / Down Steppers
            btn_up = ctk.CTkButton(
                row,
                text="▲",
                width=24,
                height=22,
                font=ctk.CTkFont(size=10),
                fg_color=theme.BG_TERTIARY if idx > 0 else theme.CARD_BG,
                hover_color=theme.ACCENT_BLUE if idx > 0 else theme.CARD_BG,
                text_color=theme.TEXT_PRIMARY if idx > 0 else theme.TEXT_MUTED,
                state="normal" if idx > 0 else "disabled",
                command=lambda i=idx: self._on_move_account(i, -1)
            )
            btn_up.pack(side="left", padx=1)

            btn_down = ctk.CTkButton(
                row,
                text="▼",
                width=24,
                height=22,
                font=ctk.CTkFont(size=10),
                fg_color=theme.BG_TERTIARY if idx < total - 1 else theme.CARD_BG,
                hover_color=theme.ACCENT_BLUE if idx < total - 1 else theme.CARD_BG,
                text_color=theme.TEXT_PRIMARY if idx < total - 1 else theme.TEXT_MUTED,
                state="normal" if idx < total - 1 else "disabled",
                command=lambda i=idx: self._on_move_account(i, 1)
            )
            btn_down.pack(side="left", padx=(1, 6))

            def_tag = " (DEFAULT)" if acc.is_default else ""
            pwd_tag = "" if acc.password else " [Copy only / no pwd]"
            lbl = ctk.CTkLabel(row, text=f"#{acc.id}: {acc.username}{pwd_tag}{def_tag}", font=ctk.CTkFont(weight="bold"))
            lbl.pack(side="left", padx=6, pady=6)

            # Bind drag & drop
            drag_handle.bind("<Button-1>", lambda e, i=idx: self._on_acc_drag_start(e, i))
            drag_handle.bind("<B1-Motion>", self._on_acc_drag_motion)
            drag_handle.bind("<ButtonRelease-1>", self._on_acc_drag_release)
            lbl.bind("<Button-1>", lambda e, i=idx: self._on_acc_drag_start(e, i))
            lbl.bind("<B1-Motion>", self._on_acc_drag_motion)
            lbl.bind("<ButtonRelease-1>", self._on_acc_drag_release)

            del_btn = ctk.CTkButton(
                row,
                text="Delete",
                width=65,
                height=26,
                fg_color=theme.ACCENT_RED,
                hover_color=theme.ACCENT_RED_HOVER,
                command=lambda a_id=acc.id: self._on_delete_account(a_id)
            )
            del_btn.pack(side="right", padx=4)

            edit_btn = ctk.CTkButton(
                row,
                text="Edit",
                width=55,
                height=26,
                fg_color=theme.ACCENT_BLUE,
                hover_color=theme.ACCENT_BLUE_HOVER,
                command=lambda a_id=acc.id: self._on_edit_account(a_id)
            )
            edit_btn.pack(side="right", padx=4)

            if not acc.is_default:
                set_def_btn = ctk.CTkButton(
                    row,
                    text="Set default",
                    width=85,
                    height=26,
                    command=lambda a_id=acc.id: self._on_set_default_account(a_id)
                )
                set_def_btn.pack(side="right", padx=4)

    def _on_acc_drag_start(self, event, idx: int):
        self._drag_acc_idx = idx
        if 0 <= idx < len(self._acc_rows):
            row_frame, _ = self._acc_rows[idx]
            row_frame.configure(border_color=theme.ACCENT_BLUE, border_width=2)

    def _on_acc_drag_motion(self, event):
        if self._drag_acc_idx is None:
            return
        target_idx = self._find_acc_drop_target(event.y_root)
        for row_frame, i in self._acc_rows:
            if i == self._drag_acc_idx:
                row_frame.configure(border_color=theme.ACCENT_BLUE, border_width=2)
            elif i == target_idx:
                row_frame.configure(border_color=theme.ACCENT_GREEN, border_width=2)
            else:
                row_frame.configure(border_color=theme.CARD_BORDER, border_width=1)

    def _find_acc_drop_target(self, y_root: int) -> Optional[int]:
        for row_frame, idx in self._acc_rows:
            try:
                ry = row_frame.winfo_rooty()
                rh = row_frame.winfo_height()
                if ry <= y_root <= ry + rh:
                    return idx
            except Exception:
                pass
        return None

    def _on_acc_drag_release(self, event):
        if self._drag_acc_idx is None:
            return
        src_idx = self._drag_acc_idx
        self._drag_acc_idx = None
        target_idx = self._find_acc_drop_target(event.y_root)

        for row_frame, _ in self._acc_rows:
            try:
                row_frame.configure(border_color=theme.CARD_BORDER, border_width=1)
            except Exception:
                pass

        if target_idx is not None and target_idx != src_idx and 0 <= target_idx < len(self.config.accounts):
            acc = self.config.accounts.pop(src_idx)
            self.config.accounts.insert(target_idx, acc)
            self.config_mgr.renumber_accounts()
            self._refresh_accounts_view()
            self._refresh_header_combo()
            self._refresh_copy_account_combo()
            self.log(f"Moved account '{acc.username}' to position #{target_idx + 1}.", "info")

    def _on_move_account(self, idx: int, delta: int):
        new_idx = idx + delta
        if 0 <= new_idx < len(self.config.accounts):
            acc = self.config.accounts.pop(idx)
            self.config.accounts.insert(new_idx, acc)
            self.config_mgr.renumber_accounts()
            self._refresh_accounts_view()
            self._refresh_header_combo()
            self._refresh_copy_account_combo()
            self.log(f"Moved account '{acc.username}' to position #{new_idx + 1}.", "info")

    def _center_toplevel(self, toplevel, width: int, height: int):
        self.root.update_idletasks()
        rx = self.root.winfo_x()
        ry = self.root.winfo_y()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        x = max(0, rx + (rw - width) // 2)
        y = max(0, ry + (rh - height) // 2)
        toplevel.geometry(f"{width}x{height}+{x}+{y}")

    def _on_edit_account(self, acc_id: str):
        target_acc = next((a for a in self.config.accounts if a.id == acc_id), None)
        if not target_acc:
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Edit account")
        dialog.geometry("380x280")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        self._center_toplevel(dialog, 380, 280)

        ctk.CTkLabel(dialog, text=f"Edit account #{target_acc.id}", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(16, 12))

        form = ctk.CTkFrame(dialog, fg_color="transparent")
        form.pack(fill="x", padx=24, pady=4)

        ctk.CTkLabel(form, text="Username:").grid(row=0, column=0, sticky="w", pady=6)
        entry_user = ctk.CTkEntry(form, width=200)
        entry_user.insert(0, target_acc.username)
        entry_user.grid(row=0, column=1, pady=6, padx=(8, 0))

        ctk.CTkLabel(form, text="Password:").grid(row=1, column=0, sticky="w", pady=6)
        entry_pwd = ctk.CTkEntry(form, width=200, show="*")
        if target_acc.password:
            entry_pwd.insert(0, target_acc.password)
        entry_pwd.grid(row=1, column=1, pady=6, padx=(8, 0))

        var_default = ctk.BooleanVar(value=target_acc.is_default)
        chk_def = ctk.CTkCheckBox(form, text="Default account", variable=var_default)
        chk_def.grid(row=2, column=1, sticky="w", pady=10, padx=(8, 0))

        btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_box.pack(pady=16)

        def save():
            new_user = entry_user.get().strip()
            if not new_user:
                messagebox.showwarning("Account", "Username cannot be empty.", parent=dialog)
                return
            new_pwd = entry_pwd.get().strip()
            old_user = target_acc.username
            target_acc.username = new_user
            target_acc.password = new_pwd

            if var_default.get():
                for a in self.config.accounts:
                    a.is_default = (a.id == target_acc.id)
            elif target_acc.is_default:
                if len(self.config.accounts) == 1:
                    target_acc.is_default = True

            self.config_mgr.save()
            self._refresh_accounts_view()
            self._refresh_header_combo()
            self._refresh_copy_account_combo()
            self.log(f"Account '{old_user}' updated to '{new_user}'.", "success")
            dialog.destroy()

        ctk.CTkButton(btn_box, text="Save", width=100, fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_HOVER, command=save).pack(side="left", padx=6)
        ctk.CTkButton(btn_box, text="Cancel", width=80, fg_color=theme.CARD_BORDER, command=dialog.destroy).pack(side="left", padx=6)

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
        self.txt_console = ctk.CTkTextbox(self.bottom_frame, height=125, font=("Courier", 12), wrap="word")
        self.txt_console.pack(fill="both", expand=True, padx=10, pady=(4, 8))

    # --------------------------------------------------------------------------
    # Logging & Console Helpers
    # --------------------------------------------------------------------------

    def log(self, message: str, level: str = "info"):
        """Thread-safe batched logging into console with timestamps."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        with self._log_lock:
            self._log_buffer.append(line)
            if not self._log_flush_pending:
                self._log_flush_pending = True
                self.root.after(35, self._flush_log_buffer)

    def _flush_log_buffer(self):
        with self._log_lock:
            lines = self._log_buffer[:]
            self._log_buffer.clear()
            self._log_flush_pending = False
        if not lines:
            return
        chunk = "".join(lines)
        try:
            self.txt_console.insert("end", chunk)
            line_count = int(self.txt_console.index('end-1c').split('.')[0])
            if line_count > 2500:
                self.txt_console.delete("1.0", f"{line_count - 2000}.0")
            self.txt_console.see("end")
        except Exception:
            pass

    def _clear_console(self):
        with self._log_lock:
            self._log_buffer.clear()
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

        if self.config.filters.omit_virtual_earth_webcam:
            orig_count = len(self.loaded_waypoints)
            self.loaded_waypoints = [w for w in self.loaded_waypoints if not is_virtual_or_earth(w.cache_type)]
            omitted = orig_count - len(self.loaded_waypoints)
            filter_msg = f" ({omitted} virtual/earth/webcam omitted by filter)" if omitted else ""
        else:
            filter_msg = ""

        count = len(self.loaded_waypoints)
        modified_hints = sum(1 for w in self.loaded_waypoints if w.is_modified_hint)
        self.lbl_coords_summary.configure(
            text=f"Loaded {count} caches ({modified_hints} flagged with modified coords in GPX){filter_msg}.",
            text_color=theme.ACCENT_GREEN if count > 0 else theme.TEXT_MUTED
        )
        self.log(f"Loaded {count} waypoints from '{os.path.basename(path)}'{filter_msg}.", "info")

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
                    resume=resume,
                    omit_virtual_and_earth=self.config.filters.omit_virtual_earth_webcam
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
            if self.config.filters.omit_virtual_earth_webcam:
                orig_c = len(items)
                items = [it for it in items if not is_virtual_or_earth(it.cache_type)]
                omitted = orig_c - len(items)
                omitted_msg = f" ({omitted} virtual/earth/webcam omitted by filter)" if omitted else ""
            else:
                omitted_msg = ""

            codes = [it.gccode for it in items]
            if codes:
                self.txt_gc_codes.insert("end", " ".join(codes) + "\n")
                self.log(f"Imported {len(codes)} GC codes from {os.path.basename(f)}{omitted_msg}.", "info")

    def _start_bulk_logging(self):
        raw_text = self.txt_gc_codes.get("1.0", "end").strip()
        codes = extract_gc_codes_from_string(raw_text)
        if not codes:
            messagebox.showwarning("Log caches", "No valid GC codes found. Please paste GC codes or import a GPX.")
            return
        if self.is_running:
            return

        mode = self.logger_mode_var.get() if hasattr(self, "logger_mode_var") else "CREATE"

        # 1. DELETE Mode
        if mode == "DELETE":
            if not messagebox.askyesno(
                "Confirm delete logs",
                f"Are you sure you want to permanently delete your log(s) for {len(codes)} cache(s)?\n\nThis cannot be undone. Continue?"
            ):
                return

            self.is_running = True
            self.btn_log_start.configure(state="disabled")
            self.progress_bar.set(0)

            logger = CacheLogger(self.client, self.safety)

            def on_progress_del(idx, total, pct, eta, gc_code):
                self.root.after(0, lambda: self._update_progress_ui(idx, total, pct, eta, f"Deleting {gc_code}"))

            def worker_delete():
                try:
                    res = logger.run_delete_log(
                        codes,
                        on_progress=on_progress_del,
                        on_log=self.log
                    )
                    self.root.after(0, lambda: self._on_operation_done("Bulk delete logs", res))
                finally:
                    self.root.after(0, self._reset_controls)

            threading.Thread(target=worker_delete, daemon=True).start()
            return

        # 2. EDIT Mode
        if mode == "EDIT":
            display_name = self.combo_log_type.get()
            keep_type = (display_name == "[Ponechat původní]")
            type_key = None
            if not keep_type:
                for k, v in LOG_TYPE_NAMES.items():
                    if v == display_name:
                        type_key = k
                        break

            date_str = self._parse_to_iso_date(self.entry_date.get())
            log_text = self.txt_log_message.get("1.0", "end").strip()
            keep_text = (log_text == "[Ponechat původní]" or not log_text)

            self.is_running = True
            self.btn_log_start.configure(state="disabled")
            self.progress_bar.set(0)

            logger = CacheLogger(self.client, self.safety)

            def on_progress_edit(idx, total, pct, eta, gc_code):
                self.root.after(0, lambda: self._update_progress_ui(idx, total, pct, eta, f"Editing {gc_code}"))

            def worker_edit():
                try:
                    res = logger.run_edit_log(
                        codes,
                        log_type_key=type_key,
                        date_str=date_str,
                        log_text=log_text,
                        keep_original_text=keep_text,
                        keep_original_type=keep_type,
                        on_progress=on_progress_edit,
                        on_log=self.log
                    )
                    self.root.after(0, lambda: self._on_operation_done("Bulk edit logs", res))
                finally:
                    self.root.after(0, self._reset_controls)

            threading.Thread(target=worker_edit, daemon=True).start()
            return

        # 3. CREATE Mode
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
                    on_log=self.log,
                    omit_virtual_and_earth=self.config.filters.omit_virtual_earth_webcam
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

        if not target_user:
            messagebox.showwarning("Copy user", "Enter a target username.")
            return

        if self.var_password_mode.get() and not target_pwd:
            messagebox.showwarning("Copy user", "Password mode is enabled, but no password was entered.\nPlease enter a password or uncheck Password mode.")
            return

        end_date_raw = self.entry_copy_date.get()
        if self.var_date_range.get():
            start_date_raw = self.entry_copy_start_date.get()
            target_date = self._parse_to_iso_date(start_date_raw)
            end_date = self._parse_to_iso_date(end_date_raw)
        else:
            target_date = self._parse_to_iso_date(end_date_raw)
            end_date = None

        def worker():
            copier = UserLogCopier(self.client, self.safety)
            codes = copier.fetch_user_logged_caches(
                target_user,
                target_pwd,
                target_date,
                on_log=self.log,
                end_date_str=end_date
            )
            if codes:
                self.root.after(0, lambda: self._insert_copied_codes(codes))
            else:
                range_txt = f"{target_date} to {end_date}" if end_date and target_date != end_date else target_date
                self.log(f"No caches found for '{target_user}' on {range_txt}.", "warning")

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_date_range(self):
        if self.var_date_range.get():
            self.lbl_copy_main_date.configure(text="End date (DD-MM-YYYY):")
            self.lbl_copy_start_date.grid(row=4, column=0, padx=6, pady=6, sticky="w")
            self.start_date_box.grid(row=4, column=1, padx=6, pady=6, sticky="w")

            current_val = self.entry_copy_date.get().strip()
            older_d = None
            for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
                try:
                    older_d = datetime.strptime(current_val, fmt).date() - timedelta(days=1)
                    break
                except ValueError:
                    pass
            if not older_d:
                older_d = datetime.now().date() - timedelta(days=1)

            self.entry_copy_start_date.delete(0, "end")
            self.entry_copy_start_date.insert(0, older_d.strftime("%d-%m-%Y"))
        else:
            self.lbl_copy_main_date.configure(text="Date (DD-MM-YYYY):")
            self.lbl_copy_start_date.grid_remove()
            self.start_date_box.grid_remove()

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

        self._tmpl_rows = []

        if not self.config.log_templates:
            ctk.CTkLabel(self.tmpl_display_frame, text="No templates configured. Add one below.", text_color=theme.TEXT_MUTED).pack(anchor="w", pady=4)
            return

        total = len(self.config.log_templates)
        for idx, tmpl in enumerate(self.config.log_templates):
            row = ctk.CTkFrame(self.tmpl_display_frame, fg_color=theme.CARD_BG, corner_radius=6, border_width=1, border_color=theme.CARD_BORDER)
            row.pack(fill="x", pady=2)
            self._tmpl_rows.append((row, idx))

            # Drag handle
            drag_handle = ctk.CTkLabel(row, text=" ☰ ", font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.TEXT_MUTED, cursor="fleur")
            drag_handle.pack(side="left", padx=(8, 2))

            # Up / Down Steppers
            btn_up = ctk.CTkButton(
                row,
                text="▲",
                width=24,
                height=22,
                font=ctk.CTkFont(size=10),
                fg_color=theme.BG_TERTIARY if idx > 0 else theme.CARD_BG,
                hover_color=theme.ACCENT_BLUE if idx > 0 else theme.CARD_BG,
                text_color=theme.TEXT_PRIMARY if idx > 0 else theme.TEXT_MUTED,
                state="normal" if idx > 0 else "disabled",
                command=lambda i=idx: self._on_move_template(i, -1)
            )
            btn_up.pack(side="left", padx=1)

            btn_down = ctk.CTkButton(
                row,
                text="▼",
                width=24,
                height=22,
                font=ctk.CTkFont(size=10),
                fg_color=theme.BG_TERTIARY if idx < total - 1 else theme.CARD_BG,
                hover_color=theme.ACCENT_BLUE if idx < total - 1 else theme.CARD_BG,
                text_color=theme.TEXT_PRIMARY if idx < total - 1 else theme.TEXT_MUTED,
                state="normal" if idx < total - 1 else "disabled",
                command=lambda i=idx: self._on_move_template(i, 1)
            )
            btn_down.pack(side="left", padx=(1, 6))

            display_snip = tmpl.replace("\n", " ").strip()
            if len(display_snip) > 55:
                display_snip = display_snip[:52] + "..."

            lbl = ctk.CTkLabel(row, text=f"#{idx + 1}: {display_snip}", font=ctk.CTkFont(weight="bold"))
            lbl.pack(side="left", padx=6, pady=6)

            # Bind drag & drop
            drag_handle.bind("<Button-1>", lambda e, i=idx: self._on_tmpl_drag_start(e, i))
            drag_handle.bind("<B1-Motion>", self._on_tmpl_drag_motion)
            drag_handle.bind("<ButtonRelease-1>", self._on_tmpl_drag_release)
            lbl.bind("<Button-1>", lambda e, i=idx: self._on_tmpl_drag_start(e, i))
            lbl.bind("<B1-Motion>", self._on_tmpl_drag_motion)
            lbl.bind("<ButtonRelease-1>", self._on_tmpl_drag_release)

            del_btn = ctk.CTkButton(
                row,
                text="Delete",
                width=65,
                height=26,
                fg_color=theme.ACCENT_RED,
                hover_color=theme.ACCENT_RED_HOVER,
                command=lambda t_idx=idx: self._on_delete_template(t_idx)
            )
            del_btn.pack(side="right", padx=4)

            edit_btn = ctk.CTkButton(
                row,
                text="Edit",
                width=55,
                height=26,
                fg_color=theme.ACCENT_BLUE,
                hover_color=theme.ACCENT_BLUE_HOVER,
                command=lambda t_idx=idx: self._on_edit_template(t_idx)
            )
            edit_btn.pack(side="right", padx=4)

    def _on_tmpl_drag_start(self, event, idx: int):
        self._drag_tmpl_idx = idx
        if 0 <= idx < len(self._tmpl_rows):
            row_frame, _ = self._tmpl_rows[idx]
            row_frame.configure(border_color=theme.ACCENT_BLUE, border_width=2)

    def _on_tmpl_drag_motion(self, event):
        if self._drag_tmpl_idx is None:
            return
        target_idx = self._find_tmpl_drop_target(event.y_root)
        for row_frame, i in self._tmpl_rows:
            if i == self._drag_tmpl_idx:
                row_frame.configure(border_color=theme.ACCENT_BLUE, border_width=2)
            elif i == target_idx:
                row_frame.configure(border_color=theme.ACCENT_GREEN, border_width=2)
            else:
                row_frame.configure(border_color=theme.CARD_BORDER, border_width=1)

    def _find_tmpl_drop_target(self, y_root: int) -> Optional[int]:
        for row_frame, idx in self._tmpl_rows:
            try:
                ry = row_frame.winfo_rooty()
                rh = row_frame.winfo_height()
                if ry <= y_root <= ry + rh:
                    return idx
            except Exception:
                pass
        return None

    def _on_tmpl_drag_release(self, event):
        if self._drag_tmpl_idx is None:
            return
        src_idx = self._drag_tmpl_idx
        self._drag_tmpl_idx = None
        target_idx = self._find_tmpl_drop_target(event.y_root)

        for row_frame, _ in self._tmpl_rows:
            try:
                row_frame.configure(border_color=theme.CARD_BORDER, border_width=1)
            except Exception:
                pass

        if target_idx is not None and target_idx != src_idx and 0 <= target_idx < len(self.config.log_templates):
            tmpl = self.config.log_templates.pop(src_idx)
            self.config.log_templates.insert(target_idx, tmpl)
            self.config_mgr.save()
            self._refresh_templates_view()
            self._update_combo_template_values()
            self.log(f"Moved template to position #{target_idx + 1}.", "info")

    def _on_move_template(self, idx: int, delta: int):
        new_idx = idx + delta
        if 0 <= new_idx < len(self.config.log_templates):
            tmpl = self.config.log_templates.pop(idx)
            self.config.log_templates.insert(new_idx, tmpl)
            self.config_mgr.save()
            self._refresh_templates_view()
            self._update_combo_template_values()
            self.log(f"Moved template to position #{new_idx + 1}.", "info")

    def _update_combo_template_values(self):
        if hasattr(self, "combo_template"):
            mode = self.logger_mode_var.get() if hasattr(self, "logger_mode_var") else "CREATE"
            if mode == "EDIT":
                self.combo_template.configure(values=["[Ponechat původní]"] + self.config.log_templates)
            else:
                self.combo_template.configure(values=self.config.log_templates)

    def _on_edit_template(self, index: int):
        if not (0 <= index < len(self.config.log_templates)):
            return

        current_val = self.config.log_templates[index]

        dialog = ctk.CTkToplevel(self.root)
        dialog.title(f"Edit template #{index + 1}")
        dialog.geometry("450x300")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        self._center_toplevel(dialog, 450, 300)

        ctk.CTkLabel(dialog, text=f"Edit template #{index + 1}", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(14, 8))

        txt_tmpl = ctk.CTkTextbox(dialog, height=150, wrap="word")
        txt_tmpl.pack(fill="both", expand=True, padx=20, pady=4)
        txt_tmpl.insert("1.0", current_val)

        btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_box.pack(pady=12)

        def save():
            new_text = txt_tmpl.get("1.0", "end").strip()
            if not new_text:
                messagebox.showwarning("Log template", "Template text cannot be empty.", parent=dialog)
                return
            self.config.log_templates[index] = new_text
            self.config_mgr.save()
            self._refresh_templates_view()
            self._update_combo_template_values()
            self.log(f"Updated template #{index + 1}.", "success")
            dialog.destroy()

        ctk.CTkButton(btn_box, text="Save", width=100, fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_HOVER, command=save).pack(side="left", padx=6)
        ctk.CTkButton(btn_box, text="Cancel", width=80, fg_color=theme.CARD_BORDER, command=dialog.destroy).pack(side="left", padx=6)

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
        self._update_combo_template_values()
        self.log(f"Added log template #{len(self.config.log_templates)}.", "success")

    def _on_delete_template(self, index: int):
        if 0 <= index < len(self.config.log_templates):
            tmpl_text = self.config.log_templates[index]
            display_snip = (tmpl_text[:40] + "...") if len(tmpl_text) > 40 else tmpl_text
            if messagebox.askyesno("Delete template", f"Remove template #{index + 1}?\n\"{display_snip}\""):
                self.config.log_templates.pop(index)
                if not self.config.log_templates:
                    self.config.log_templates = ["TFTC."]
                self.config_mgr.save()
                self._refresh_templates_view()
                self._update_combo_template_values()
                self.log(f"Removed template #{index + 1}.", "info")

    def _on_toggle_omit_virtual_earth(self):
        self.config.filters.omit_virtual_earth_webcam = self.var_omit_virtual_earth.get()
        self.config_mgr.save()
        status = "enabled" if self.config.filters.omit_virtual_earth_webcam else "disabled"
        self.log(f"Filter 'Omit virtual, earth, and webcam caches' {status}.", "info")

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

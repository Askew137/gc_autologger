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

from core.config import (
    ConfigManager, AccountConfig, AnswerTemplate, AiConfig,
    SystemPromptConfig, get_default_system_prompts, get_base_dir, get_bundle_dir
)
from core.client import GeocachingClient
from core.safety import SafetyManager
from core.checkpoint import RunCheckpointManager
from core.gpx_parser import parse_file, parse_folder, extract_gc_codes_from_string, WaypointItem, is_virtual_or_earth, is_cache_filtered
from core.ai_service import AiService
from operations.coordinate_uploader import CoordinateUploader
from operations.cache_logger import CacheLogger, LOG_TYPE_MAP, LOG_TYPE_NAMES, extract_answers_from_note, format_answer_message
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
        self.selected_coords_files: List[str] = []
        self.selected_ignore_files: List[str] = []
        self.is_running = False
        self.ignore_source_list_code: Optional[str] = None
        self.var_empty_source_list = ctk.BooleanVar(value=False)

        # Console log buffering for high UI responsiveness
        self._log_buffer: List[str] = []
        self._log_lock = threading.Lock()
        self._log_flush_pending = False

        # Drag-and-drop state
        self._drag_acc_idx: Optional[int] = None
        self._acc_rows: List[tuple] = []
        self._drag_tmpl_idx: Optional[int] = None
        self._tmpl_rows: List[tuple] = []
        self._ans_tmpl_rows: List[tuple] = []
        self._drag_ans_tmpl_idx: Optional[int] = None
        self._sys_prompt_rows: List[tuple] = []
        self._drag_sys_prompt_idx: Optional[int] = None

        # Send concepts & answers approval state
        self.var_send_answers = ctk.BooleanVar(value=self.config.send_answers)
        self._approval_event = threading.Event()
        self._approval_result: Tuple[str, str, str] = ("SKIP", "", "")
        self._current_approval_data: Dict[str, Any] = {}

        self._init_ui()
        self._set_app_icon()
        self._init_client_session()
        self._check_active_checkpoint()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_window)

    def _on_close_window(self):
        if self.is_running:
            if not messagebox.askyesno("Exit", "An operation is currently running. Stop and exit?", parent=self.root):
                return
            self.safety.cancel()
        self.root.destroy()

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

        self.coords_path_entry = ctk.CTkEntry(file_frame, placeholder_text="Select GPX/LOC file(s)...")
        self.coords_path_entry.pack(side="left", fill="x", expand=True, padx=8)
        if getattr(self.config, "last_browse_dir", None):
            pass

        ctk.CTkButton(file_frame, text="Browse file(s)", width=110, command=self._browse_coords_file).pack(side="left", padx=4)
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

        # Row 0: Target data selection (Coordinates / Personal Notes)
        ctk.CTkLabel(self.coords_opt_frame, text="Upload data:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=10, pady=(8, 4), sticky="w")

        self.var_upload_coords = ctk.BooleanVar(value=True)
        self.chk_upload_coords = ctk.CTkCheckBox(
            self.coords_opt_frame,
            text="Upload coordinates",
            variable=self.var_upload_coords,
            command=self._on_coords_options_changed
        )
        self.chk_upload_coords.grid(row=0, column=1, padx=10, pady=(8, 4), sticky="w")

        self.var_upload_notes = ctk.BooleanVar(value=True)
        self.chk_upload_notes = ctk.CTkCheckBox(
            self.coords_opt_frame,
            text="Upload personal notes",
            variable=self.var_upload_notes,
            command=self._on_coords_options_changed
        )
        self.chk_upload_notes.grid(row=0, column=2, padx=15, pady=(8, 4), sticky="w")

        # Row 1: Upload mode selector (Complete overwrite / Only unmodified)
        self.lbl_coords_mode = ctk.CTkLabel(self.coords_opt_frame, text="Upload mode:", font=ctk.CTkFont(weight="bold"))
        self.lbl_coords_mode.grid(row=1, column=0, padx=10, pady=(4, 8), sticky="w")

        self.coords_mode_var = ctk.StringVar(value=CoordinateUploader.MODE_OVERWRITE_ALL)
        self.rad_coords_overwrite = ctk.CTkRadioButton(
            self.coords_opt_frame,
            text="Complete overwrite (always upload / replace on web)",
            variable=self.coords_mode_var,
            value=CoordinateUploader.MODE_OVERWRITE_ALL
        )
        self.rad_coords_overwrite.grid(row=1, column=1, padx=10, pady=(4, 8), sticky="w")

        self.rad_coords_unmodified = ctk.CTkRadioButton(
            self.coords_opt_frame,
            text="Only unmodified (skip caches already set on web)",
            variable=self.coords_mode_var,
            value=CoordinateUploader.MODE_UNMODIFIED_ONLY
        )
        self.rad_coords_unmodified.grid(row=1, column=2, padx=15, pady=(4, 8), sticky="w")


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

    def _on_coords_options_changed(self):
        upload_coords = self.var_upload_coords.get()
        upload_notes = self.var_upload_notes.get()
        has_any = upload_coords or upload_notes

        # Toggle upload mode radio buttons availability
        radio_state = "normal" if has_any else "disabled"
        self.rad_coords_overwrite.configure(state=radio_state)
        self.rad_coords_unmodified.configure(state=radio_state)
        self.lbl_coords_mode.configure(text_color=theme.TEXT_PRIMARY if has_any else theme.TEXT_MUTED)

        # Start button enabled only if at least one checkbox is selected
        if not has_any:
            self.btn_coords_start.configure(state="disabled")
        elif not self.is_running:
            self.btn_coords_start.configure(state="normal")

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
            values=["Create logs", "Edit logs", "Delete logs", "Send concepts"],
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
            text="Delete Mode:\nPermanently deletes your existing logs on Geocaching.com\nfor all listed GC codes.\n\nNo date, type, or log text input required.",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#fca5a5",
            justify="left"
        ).pack(padx=16, pady=20)

        # Send concepts mode informational frame (shown only in Send concepts mode)
        self.frame_concepts_info = ctk.CTkFrame(
            self.logger_left_box,
            fg_color="#182234",
            border_width=1,
            border_color="#38bdf8",
            corner_radius=6
        )
        ctk.CTkLabel(
            self.frame_concepts_info,
            text="Send Concepts (Drafts) Mode:\n\n"
                 "Automatically fetches all pending log drafts from your\n"
                 "Geocaching.com account and submits them as official logs.\n\n"
                 "• Dates, log types, and notes written in the field\n"
                 "  are preserved exactly as recorded.\n\n"
                 "Click Start below to begin sending concepts.",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#bae6fd",
            justify="left"
        ).pack(padx=20, pady=(20, 10))

        self.chk_send_answers = ctk.CTkCheckBox(
            self.frame_concepts_info,
            text="Send answers to cache owners (EarthCache / Virtual)",
            font=ctk.CTkFont(size=12, weight="bold"),
            variable=self.var_send_answers,
            command=self._on_toggle_send_answers
        )
        self.chk_send_answers.pack(anchor="w", padx=20, pady=(0, 20))

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

        # Right side: Target GC codes & Answers approval
        self.logger_right_box = ctk.CTkFrame(log_grid)
        self.logger_right_box.pack(side="right", fill="both", expand=True, padx=5, pady=5)

        # Standard GC codes input container
        self.frame_gc_codes_input = ctk.CTkFrame(self.logger_right_box, fg_color="transparent")
        self.frame_gc_codes_input.pack(fill="both", expand=True)

        ctk.CTkLabel(self.frame_gc_codes_input, text="Target GC codes (from GPX or paste here):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=6)
        self.txt_gc_codes = ctk.CTkTextbox(self.frame_gc_codes_input, height=170, wrap="word")
        self.txt_gc_codes.pack(fill="both", expand=True, padx=10, pady=4)

        btn_row = ctk.CTkFrame(self.frame_gc_codes_input, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(4, 8))
        ctk.CTkButton(btn_row, text="Import from GPX", width=120, command=self._import_codes_from_file).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="Import from List", width=120, command=lambda: self._open_gc_list_import_dialog("bulk")).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="Clear codes", width=90, fg_color=theme.CARD_BORDER, command=lambda: self.txt_gc_codes.delete("1.0", "end")).pack(side="left", padx=4)

        # Answers Approval Panel (shown when Send concepts + Send answers is active)
        self.frame_answers_approval = ctk.CTkFrame(self.logger_right_box, fg_color="transparent")

        # 1. Header Card
        self.lbl_approval_cache_info = ctk.CTkLabel(
            self.frame_answers_approval,
            text="⏳ Waiting for EarthCache / Virtual Cache concepts...",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#38bdf8",
            justify="left",
            anchor="w"
        )
        self.lbl_approval_cache_info.pack(fill="x", padx=8, pady=(4, 6))

        # 2. Template Selector Row
        tmpl_sel_row = ctk.CTkFrame(self.frame_answers_approval, fg_color="transparent")
        tmpl_sel_row.pack(fill="x", padx=8, pady=(0, 6))
        ctk.CTkLabel(tmpl_sel_row, text="Template:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(0, 6))
        ans_tmpl_names = [t.name for t in self.config.answer_templates] or ["Default"]
        self.combo_answer_templates = ctk.CTkComboBox(
            tmpl_sel_row,
            values=ans_tmpl_names,
            width=260,
            command=self._on_approval_template_changed
        )
        if self.config.selected_answer_template in ans_tmpl_names:
            self.combo_answer_templates.set(self.config.selected_answer_template)
        elif ans_tmpl_names:
            self.combo_answer_templates.set(ans_tmpl_names[0])
        self.combo_answer_templates.pack(side="left")

        # 3. Answers Editor Area
        ctk.CTkLabel(
            self.frame_answers_approval,
            text="Answers (replaces [...] in template):",
            font=ctk.CTkFont(size=12, weight="bold")
        ).pack(anchor="w", padx=8, pady=(2, 2))

        ans_editor_frame = ctk.CTkFrame(self.frame_answers_approval, fg_color="transparent")
        ans_editor_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self.txt_approval_answers = ctk.CTkTextbox(ans_editor_frame, height=130, wrap="word")
        self.txt_approval_answers.pack(side="left", fill="both", expand=True)
        self.txt_approval_answers.bind("<KeyRelease>", lambda e: self._update_approval_preview())

        # Vertical 90-degree rotated button stack on the right of the textbox
        ans_btn_stack = ctk.CTkFrame(ans_editor_frame, fg_color="transparent")
        ans_btn_stack.pack(side="right", fill="y", padx=(6, 0))

        ctk.CTkButton(
            ans_btn_stack,
            text="Blank",
            width=65,
            height=28,
            fg_color=theme.CARD_BORDER,
            hover_color=theme.CARD_BG,
            command=self._on_ans_blank_clicked
        ).pack(pady=2)

        ctk.CTkButton(
            ans_btn_stack,
            text="Note",
            width=65,
            height=28,
            fg_color=theme.ACCENT_BLUE,
            hover_color=theme.ACCENT_BLUE_HOVER,
            command=self._on_ans_note_clicked
        ).pack(pady=2)

        ctk.CTkButton(
            ans_btn_stack,
            text="AI",
            width=65,
            height=28,
            fg_color="#8b5cf6",
            hover_color="#7c3aed",
            command=self._on_ans_ai_clicked
        ).pack(pady=2)

        # 4. Clipboard & Message Preview Row
        clip_row = ctk.CTkFrame(self.frame_answers_approval, fg_color="transparent")
        clip_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkButton(
            clip_row,
            text="Copy listing & note to clipboard",
            width=240,
            height=28,
            fg_color=theme.CARD_BORDER,
            hover_color=theme.CARD_BG,
            command=self._on_copy_listing_to_clipboard
        ).pack(side="left")

        ctk.CTkLabel(
            self.frame_answers_approval,
            text="Full message preview:",
            font=ctk.CTkFont(size=11),
            text_color=theme.TEXT_MUTED
        ).pack(anchor="w", padx=8, pady=(4, 0))

        self.txt_approval_preview = ctk.CTkTextbox(
            self.frame_answers_approval,
            height=65,
            wrap="word",
            fg_color="#181c24",
            text_color="#94a3b8"
        )
        self.txt_approval_preview.pack(fill="x", padx=8, pady=(2, 6))
        self.txt_approval_preview.configure(state="disabled")

        # 5. Bottom Action Buttons
        approval_btn_row = ctk.CTkFrame(self.frame_answers_approval, fg_color="transparent")
        approval_btn_row.pack(fill="x", padx=8, pady=(2, 6))

        self.btn_send_answer = ctk.CTkButton(
            approval_btn_row,
            text="Send answer & Log",
            font=ctk.CTkFont(weight="bold"),
            fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            state="disabled",
            height=32,
            command=self._on_approve_send_clicked
        )
        self.btn_send_answer.pack(side="left", padx=(0, 6))

        self.btn_skip_answer = ctk.CTkButton(
            approval_btn_row,
            text="Skip answer & Log",
            font=ctk.CTkFont(weight="bold"),
            fg_color="#d97706",
            hover_color="#b45309",
            state="disabled",
            height=32,
            command=self._on_approve_skip_clicked
        )
        self.btn_skip_answer.pack(side="left")

        # Resume banner frame (shown if an unfinished logging run is detected)
        self.frame_resume_logging = ctk.CTkFrame(
            self.tab_logger,
            fg_color="#2b2613",
            border_width=1,
            border_color="#d48806",
            corner_radius=6
        )
        self.lbl_resume_logging_info = ctk.CTkLabel(
            self.frame_resume_logging,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#f5c056"
        )
        self.lbl_resume_logging_info.pack(side="left", padx=12, pady=8)

        self.btn_discard_logging_action = ctk.CTkButton(
            self.frame_resume_logging,
            text="Discard",
            width=75,
            height=28,
            fg_color=theme.ACCENT_RED,
            command=self._on_discard_logging_clicked
        )
        self.btn_discard_logging_action.pack(side="right", padx=8, pady=6)

        self.btn_resume_logging_action = ctk.CTkButton(
            self.frame_resume_logging,
            text="▶ Resume logging",
            width=125,
            height=28,
            fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            command=self._on_resume_logging_clicked
        )
        self.btn_resume_logging_action.pack(side="right", padx=4, pady=6)

        # Control buttons (Start / Pause / Stop)
        ctrl_frame_log = ctk.CTkFrame(self.tab_logger, fg_color="transparent")
        ctrl_frame_log.pack(fill="x", padx=14, pady=8)

        self.btn_log_start = ctk.CTkButton(
            ctrl_frame_log,
            text="▶ Start bulk logging",
            font=ctk.CTkFont(weight="bold"),
            fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            height=36,
            width=180,
            command=self._start_bulk_logging
        )
        self.btn_log_start.pack(side="left", padx=(0, 6))

        self.btn_log_pause = ctk.CTkButton(
            ctrl_frame_log,
            text="⏸ Pause",
            width=90,
            height=36,
            state="disabled",
            command=self._toggle_pause
        )
        self.btn_log_pause.pack(side="left", padx=6)

        self.btn_log_stop = ctk.CTkButton(
            ctrl_frame_log,
            text="⏹ Stop",
            width=90,
            height=36,
            fg_color=theme.ACCENT_RED,
            state="disabled",
            command=self._stop_operation
        )
        self.btn_log_stop.pack(side="left", padx=6)

    def _on_logger_mode_changed(self, mode_val: str):
        # Manage right box visibility (hidden in Send concepts mode unless send_answers is checked)
        if hasattr(self, "logger_right_box") and hasattr(self, "frame_gc_codes_input") and hasattr(self, "frame_answers_approval"):
            if mode_val == "Send concepts":
                self.frame_gc_codes_input.pack_forget()
                if self.var_send_answers.get():
                    self.frame_answers_approval.pack(fill="both", expand=True)
                    self.logger_right_box.pack(side="right", fill="both", expand=True, padx=5, pady=5)
                else:
                    self.frame_answers_approval.pack_forget()
                    self.logger_right_box.pack_forget()
            else:
                self.frame_answers_approval.pack_forget()
                self.frame_gc_codes_input.pack(fill="both", expand=True)
                self.logger_right_box.pack(side="right", fill="both", expand=True, padx=5, pady=5)

        if hasattr(self, "frame_concepts_info"):
            self.frame_concepts_info.grid_remove()

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
                text="▶ Start editing logs",
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
                text="▶ Delete logs",
                fg_color=theme.ACCENT_RED,
                hover_color=theme.ACCENT_RED_HOVER
            )

        elif mode_val == "Send concepts":
            self.logger_mode_var.set("CONCEPTS")
            self.frame_delete_info.grid_remove()
            self.lbl_log_type.grid_remove()
            self.combo_log_type.grid_remove()
            self.lbl_log_date.grid_remove()
            self.date_box.grid_remove()
            self.lbl_log_tmpl.grid_remove()
            self.combo_template.grid_remove()
            self.lbl_log_msg.grid_remove()
            self.txt_log_message.grid_remove()

            self.frame_concepts_info.grid(row=0, column=0, columnspan=2, padx=10, pady=20, sticky="nsew")

            self.btn_log_start.configure(
                text="▶ Start sending concepts",
                fg_color=theme.ACCENT_GREEN,
                hover_color=theme.ACCENT_GREEN_HOVER
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

        ctk.CTkLabel(box, text="Ignore list management (Add / Remove caches)", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=14, pady=(12, 6))

        # Import buttons row (matching Bulk Logging design)
        import_row = ctk.CTkFrame(box, fg_color="transparent")
        import_row.pack(fill="x", padx=14, pady=6)

        ctk.CTkButton(import_row, text="Import from GPX", width=120, command=self._browse_ignore_file).pack(side="left", padx=4)
        ctk.CTkButton(import_row, text="Import from List", width=120, command=lambda: self._open_gc_list_import_dialog("ignore")).pack(side="left", padx=4)
        ctk.CTkButton(import_row, text="Fetch ignored caches", width=145, fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_HOVER, command=self._fetch_ignored_caches).pack(side="left", padx=4)
        ctk.CTkButton(import_row, text="Clear codes", width=90, fg_color=theme.CARD_BORDER, command=lambda: self.txt_ignore_codes.delete("1.0", "end")).pack(side="left", padx=4)

        # Text input & review
        ctk.CTkLabel(box, text="GC codes to process (from imported files or paste here):", text_color=theme.TEXT_MUTED).pack(anchor="w", padx=14, pady=(6, 2))

        self.txt_ignore_codes = ctk.CTkTextbox(box, height=160, wrap="word")
        self.txt_ignore_codes.pack(fill="both", expand=True, padx=14, pady=4)

        btn_row = ctk.CTkFrame(box, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=8)

        ctk.CTkButton(
            btn_row,
            text="Add to ignore list",
            font=ctk.CTkFont(weight="bold"),
            fg_color=theme.ACCENT_RED,
            hover_color=theme.ACCENT_RED_HOVER,
            height=36,
            command=self._start_bulk_ignore
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_row,
            text="Remove from ignore list (Unignore)",
            font=ctk.CTkFont(weight="bold"),
            fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            height=36,
            command=self._start_bulk_unignore
        ).pack(side="left", padx=4)

        self.chk_ignore_empty_source = ctk.CTkCheckBox(
            btn_row,
            text="Empty source list on ignore",
            font=ctk.CTkFont(size=12),
            variable=self.var_empty_source_list
        )
        self.chk_ignore_empty_source.pack(side="left", padx=10)

    # --------------------------------------------------------------------------
    # Tab 5: Config & Accounts (Visual Settings Editor)
    # --------------------------------------------------------------------------

    def _build_tab_settings(self):
        scroll = ctk.CTkScrollableFrame(self.tab_settings)
        scroll.pack(fill="both", expand=True, padx=6, pady=6)

        # Configure 1 unit = 1 pixel for smooth trackpad scrolling
        scroll._parent_canvas.configure(yscrollincrement=1)
        # Suppress CustomTkinter's default scroll handler to avoid conflicts
        scroll._check_if_valid_scroll = lambda w: False

        def _can_scroll_settings(event):
            try:
                if self.root.grab_current() is not None:
                    return False
                if hasattr(self, "tabview"):
                    cur_tab = str(self.tabview.get()).lower()
                    if "config" not in cur_tab and "account" not in cur_tab:
                        return False
                w = getattr(event, "widget", None)
                if w is not None and hasattr(self, "txt_console"):
                    console_box = getattr(self.txt_console, "_textbox", self.txt_console)
                    if str(w).startswith(str(console_box)):
                        return False
                return True
            except Exception:
                return False

        def _on_touchpad_scroll(event):
            if not _can_scroll_settings(event):
                return
            raw_delta = getattr(event, "delta", 0)
            if not raw_delta:
                return
            try:
                raw = int(raw_delta) & 0xFFFFFFFF
                delta_y = raw & 0xFFFF
                if delta_y >= 0x8000:
                    delta_y -= 0x10000
                if delta_y != 0:
                    scroll._parent_canvas.yview_scroll(-delta_y, "units")
            except Exception:
                pass

        def _on_mouse_wheel(event):
            if not _can_scroll_settings(event):
                return
            raw_delta = getattr(event, "delta", 0)
            if not raw_delta:
                return
            try:
                val = int(raw_delta)
                # If delta is packed 32-bit (Tk 9 trackpad routed to mousewheel)
                if abs(val) > 1000:
                    raw = val & 0xFFFFFFFF
                    delta_y = raw & 0xFFFF
                    if delta_y >= 0x8000:
                        delta_y -= 0x10000
                    if delta_y != 0:
                        scroll._parent_canvas.yview_scroll(-delta_y, "units")
                    return

                if sys.platform == "darwin":
                    scroll_pixels = -int(val * 25)
                elif sys.platform.startswith("win"):
                    scroll_pixels = -int((val / 120) * 35)
                else:
                    scroll_pixels = -30 if val > 0 else 30

                if scroll_pixels != 0:
                    scroll._parent_canvas.yview_scroll(scroll_pixels, "units")
            except Exception:
                pass

        def _on_btn4(event):
            if _can_scroll_settings(event):
                try:
                    scroll._parent_canvas.yview_scroll(-30, "units")
                except Exception:
                    pass

        def _on_btn5(event):
            if _can_scroll_settings(event):
                try:
                    scroll._parent_canvas.yview_scroll(30, "units")
                except Exception:
                    pass

        try:
            self.root.bind_all("<TouchpadScroll>", _on_touchpad_scroll, add=True)
        except Exception:
            pass
        self.root.bind_all("<MouseWheel>", _on_mouse_wheel, add=True)
        self.root.bind_all("<Button-4>", _on_btn4, add=True)
        self.root.bind_all("<Button-5>", _on_btn5, add=True)

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

        # 3. Answer Templates Section
        ans_tmpl_sec = ctk.CTkFrame(scroll)
        ans_tmpl_sec.pack(fill="x", pady=6, padx=6)
        ctk.CTkLabel(ans_tmpl_sec, text="Answer templates (EarthCache / Virtual)", font=ctk.CTkFont(weight="bold", size=14)).pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(
            ans_tmpl_sec,
            text="Templates for sending answers to cache owners. Use [...] where answers will be inserted, and {username} for your username.",
            font=ctk.CTkFont(size=12),
            text_color=theme.TEXT_MUTED
        ).pack(anchor="w", padx=12, pady=(0, 4))

        self.ans_tmpl_display_frame = ctk.CTkFrame(ans_tmpl_sec, fg_color="transparent")
        self.ans_tmpl_display_frame.pack(fill="x", padx=12, pady=4)
        self._refresh_answer_templates_view()

        add_ans_tmpl_frame = ctk.CTkFrame(ans_tmpl_sec, fg_color="transparent")
        add_ans_tmpl_frame.pack(fill="x", padx=12, pady=(8, 12))

        ctk.CTkButton(add_ans_tmpl_frame, text="+ Add answer template", width=170, command=self._on_add_answer_template).pack(side="left", padx=4)

        # 4. Filters Section
        filter_sec = ctk.CTkFrame(scroll)
        filter_sec.pack(fill="x", pady=6, padx=6)
        ctk.CTkLabel(filter_sec, text="Filters", font=ctk.CTkFont(weight="bold", size=14)).pack(anchor="w", padx=12, pady=(10, 4))

        filter_content = ctk.CTkFrame(filter_sec, fg_color="transparent")
        filter_content.pack(fill="x", padx=12, pady=(4, 12))

        self.var_omit_virtual = ctk.BooleanVar(value=self.config.filters.omit_virtual or self.config.filters.omit_webcam)
        self.chk_omit_virtual = ctk.CTkCheckBox(
            filter_content,
            text="Omit Virtual caches (Virtual, Locationless, Webcam)",
            font=ctk.CTkFont(size=13),
            variable=self.var_omit_virtual,
            command=self._on_toggle_filters
        )
        self.chk_omit_virtual.pack(anchor="w", padx=4, pady=3)

        self.var_omit_earth = ctk.BooleanVar(value=self.config.filters.omit_earthcache)
        self.chk_omit_earth = ctk.CTkCheckBox(
            filter_content,
            text="Omit EarthCaches",
            font=ctk.CTkFont(size=13),
            variable=self.var_omit_earth,
            command=self._on_toggle_filters
        )
        self.chk_omit_earth.pack(anchor="w", padx=4, pady=3)

        self.var_omit_events = ctk.BooleanVar(value=self.config.filters.omit_events)
        self.chk_omit_events = ctk.CTkCheckBox(
            filter_content,
            text="Omit Events (Event, Mega, Giga, CITO, Celebration, GPS Maze)",
            font=ctk.CTkFont(size=13),
            variable=self.var_omit_events,
            command=self._on_toggle_filters
        )
        self.chk_omit_events.pack(anchor="w", padx=4, pady=3)

        self.var_omit_traditional = ctk.BooleanVar(value=self.config.filters.omit_traditional)
        self.chk_omit_traditional = ctk.CTkCheckBox(
            filter_content,
            text="Omit Traditional caches",
            font=ctk.CTkFont(size=13),
            variable=self.var_omit_traditional,
            command=self._on_toggle_filters
        )
        self.chk_omit_traditional.pack(anchor="w", padx=4, pady=3)

        self.var_omit_other = ctk.BooleanVar(value=self.config.filters.omit_other)
        self.chk_omit_other = ctk.CTkCheckBox(
            filter_content,
            text="Omit Other caches (Mystery, Multi, Wherigo, Letterbox)",
            font=ctk.CTkFont(size=13),
            variable=self.var_omit_other,
            command=self._on_toggle_filters
        )
        self.chk_omit_other.pack(anchor="w", padx=4, pady=3)

        ctk.CTkLabel(
            filter_content,
            text="💡 Automatically skips selected cache types during Coordinate Upload, Bulk Logging, and Copy User.",
            font=ctk.CTkFont(size=11),
            text_color=theme.TEXT_MUTED
        ).pack(anchor="w", padx=4, pady=(2, 4))

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

        # 5. Geodeep Notes Section
        geodeep_sec = ctk.CTkFrame(scroll)
        geodeep_sec.pack(fill="x", pady=6, padx=6)
        ctk.CTkLabel(geodeep_sec, text="Geodeep notes", font=ctk.CTkFont(weight="bold", size=14)).pack(anchor="w", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            geodeep_sec,
            text="Select which fields from Geodeep notes to include in personal notes uploaded to the web.\n"
                 "💡 Leaving all options unchecked copies notes as-is without any modifications.",
            font=ctk.CTkFont(size=11),
            text_color=theme.TEXT_MUTED,
            justify="left"
        ).pack(anchor="w", padx=12, pady=(0, 6))

        gd_content = ctk.CTkFrame(geodeep_sec, fg_color="transparent")
        gd_content.pack(fill="x", padx=12, pady=(4, 12))

        self.var_geodeep_date = ctk.BooleanVar(value=self.config.geodeep_notes.include_date)
        self.chk_geodeep_date = ctk.CTkCheckBox(
            gd_content,
            text="Date added",
            font=ctk.CTkFont(size=13),
            variable=self.var_geodeep_date,
            command=self._on_toggle_geodeep_notes
        )
        self.chk_geodeep_date.pack(anchor="w", padx=4, pady=3)

        self.var_geodeep_drive_in = ctk.BooleanVar(value=self.config.geodeep_notes.include_drive_in)
        self.chk_geodeep_drive_in = ctk.CTkCheckBox(
            gd_content,
            text="Drive in",
            font=ctk.CTkFont(size=13),
            variable=self.var_geodeep_drive_in,
            command=self._on_toggle_geodeep_notes
        )
        self.chk_geodeep_drive_in.pack(anchor="w", padx=4, pady=3)

        self.var_geodeep_verified = ctk.BooleanVar(value=self.config.geodeep_notes.include_verified)
        self.chk_geodeep_verified = ctk.CTkCheckBox(
            gd_content,
            text="Verified",
            font=ctk.CTkFont(size=13),
            variable=self.var_geodeep_verified,
            command=self._on_toggle_geodeep_notes
        )
        self.chk_geodeep_verified.pack(anchor="w", padx=4, pady=3)

        self.var_geodeep_notes = ctk.BooleanVar(value=self.config.geodeep_notes.include_notes)
        self.chk_geodeep_notes = ctk.CTkCheckBox(
            gd_content,
            text="Notes",
            font=ctk.CTkFont(size=13),
            variable=self.var_geodeep_notes,
            command=self._on_toggle_geodeep_notes
        )
        self.chk_geodeep_notes.pack(anchor="w", padx=4, pady=3)

        # 7. AI Assistant Settings Section
        ai_sec = ctk.CTkFrame(scroll)
        ai_sec.pack(fill="x", pady=6, padx=6)
        ctk.CTkLabel(ai_sec, text="AI Assistant (EarthCache / Virtual Answers)", font=ctk.CTkFont(weight="bold", size=14)).pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(
            ai_sec,
            text="Configure AI to generate concise answers from cache listings and notes. Supports OpenAI, MetaCentrum (CESNET / vLLM), and Google Gemini.",
            font=ctk.CTkFont(size=12),
            text_color=theme.TEXT_MUTED
        ).pack(anchor="w", padx=12, pady=(0, 6))

        ai_grid = ctk.CTkFrame(ai_sec, fg_color="transparent")
        ai_grid.pack(fill="x", padx=12, pady=4)

        # Provider
        ctk.CTkLabel(ai_grid, text="Provider:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=6, pady=4, sticky="w")
        self.ai_provider_combo = ctk.CTkComboBox(
            ai_grid,
            values=["OpenAI / MetaCentrum / Custom", "Google Gemini"],
            width=260,
            command=self._on_ai_provider_changed
        )
        current_prov = "Google Gemini" if self.config.ai.provider == "gemini" else "OpenAI / MetaCentrum / Custom"
        self.ai_provider_combo.set(current_prov)
        self.ai_provider_combo.grid(row=0, column=1, padx=6, pady=4, sticky="w")

        # Base URL
        ctk.CTkLabel(ai_grid, text="Base URL (optional):", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=6, pady=4, sticky="w")
        self.ai_base_url_entry = ctk.CTkEntry(ai_grid, width=380, placeholder_text="e.g. https://llm.metacentrum.cz/v1 (leave empty for OpenAI)")
        self.ai_base_url_entry.insert(0, self.config.ai.base_url)
        self.ai_base_url_entry.grid(row=1, column=1, padx=6, pady=4, sticky="w")

        # Model
        ctk.CTkLabel(ai_grid, text="Model name:", font=ctk.CTkFont(weight="bold")).grid(row=2, column=0, padx=6, pady=4, sticky="w")
        self.ai_model_entry = ctk.CTkEntry(ai_grid, width=260, placeholder_text="e.g. gpt-4o-mini or Qwen/Qwen2.5-72B-Instruct")
        self.ai_model_entry.insert(0, self.config.ai.model or "gpt-4o-mini")
        self.ai_model_entry.bind("<KeyRelease>", lambda e: self._update_thinking_budget_slider_limits())
        self.ai_model_entry.bind("<FocusOut>", lambda e: self._update_thinking_budget_slider_limits())
        self.ai_model_entry.grid(row=2, column=1, padx=6, pady=4, sticky="w")

        # API Key
        ctk.CTkLabel(ai_grid, text="API Key:", font=ctk.CTkFont(weight="bold")).grid(row=3, column=0, padx=6, pady=4, sticky="w")
        key_box = ctk.CTkFrame(ai_grid, fg_color="transparent")
        key_box.grid(row=3, column=1, padx=6, pady=4, sticky="w")
        self.ai_key_entry = ctk.CTkEntry(key_box, width=320, show="*", placeholder_text="Enter API key or MetaCentrum token...")
        self.ai_key_entry.insert(0, self.config.ai.api_key)
        self.ai_key_entry.pack(side="left")
        self.btn_toggle_ai_key = ctk.CTkButton(key_box, text="👁", width=36, command=self._toggle_ai_key_visibility)
        self.btn_toggle_ai_key.pack(side="left", padx=4)

        # Default Answer Source
        ctk.CTkLabel(ai_grid, text="Default source:", font=ctk.CTkFont(weight="bold")).grid(row=4, column=0, padx=6, pady=4, sticky="w")
        self.ai_default_source_combo = ctk.CTkComboBox(ai_grid, values=["Note", "AI", "Blank"], width=160)
        source_val = (self.config.default_answer_source or "note").capitalize()
        if source_val not in ["Note", "AI", "Blank"]:
            source_val = "Note"
        self.ai_default_source_combo.set(source_val)
        self.ai_default_source_combo.grid(row=4, column=1, padx=6, pady=4, sticky="w")

        # Thinking Budget Slider
        ctk.CTkLabel(ai_grid, text="Thinking budget:", font=ctk.CTkFont(weight="bold")).grid(row=5, column=0, padx=6, pady=4, sticky="w")
        tb_box = ctk.CTkFrame(ai_grid, fg_color="transparent")
        tb_box.grid(row=5, column=1, padx=6, pady=4, sticky="w")

        init_budget = getattr(self.config.ai, "thinking_budget", 1024)
        self.ai_thinking_budget_slider = ctk.CTkSlider(
            tb_box,
            from_=0,
            to=8192,
            number_of_steps=64,
            width=220,
            command=self._on_thinking_budget_changed
        )
        self.ai_thinking_budget_slider.set(init_budget)
        self.ai_thinking_budget_slider.pack(side="left")

        self.lbl_thinking_budget = ctk.CTkLabel(tb_box, text=self._format_thinking_budget(init_budget), font=ctk.CTkFont(size=12))
        self.lbl_thinking_budget.pack(side="left", padx=10)
        self._update_thinking_budget_slider_limits()

        # System Prompts management
        ctk.CTkLabel(ai_sec, text="AI System Prompts (Multilingual / Custom):", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=12, pady=(12, 4))
        ctk.CTkLabel(
            ai_sec,
            text="Manage system prompts for different languages. You can assign a specific prompt to each Answer Template.",
            font=ctk.CTkFont(size=12),
            text_color=theme.TEXT_MUTED
        ).pack(anchor="w", padx=12, pady=(0, 6))

        self.sys_prompt_display_frame = ctk.CTkFrame(ai_sec, fg_color="transparent")
        self.sys_prompt_display_frame.pack(fill="x", padx=12, pady=4)
        self._refresh_sys_prompts_view()

        add_sys_prompt_frame = ctk.CTkFrame(ai_sec, fg_color="transparent")
        add_sys_prompt_frame.pack(fill="x", padx=12, pady=(4, 10))

        ctk.CTkButton(add_sys_prompt_frame, text="+ Add system prompt", width=160, command=self._on_add_system_prompt).pack(side="left", padx=4)
        ctk.CTkButton(add_sys_prompt_frame, text="↺ Reset default prompts", width=160, fg_color=theme.CARD_BORDER, hover_color=theme.CARD_BG, command=self._on_reset_system_prompts).pack(side="left", padx=4)

        # Test Connection button
        btn_ai_row = ctk.CTkFrame(ai_sec, fg_color="transparent")
        btn_ai_row.pack(fill="x", padx=12, pady=(6, 12))
        self.btn_test_ai = ctk.CTkButton(
            btn_ai_row,
            text="Test AI Connection",
            width=160,
            fg_color=theme.CARD_BORDER,
            hover_color=theme.CARD_BG,
            command=self._on_test_ai_connection
        )
        self.btn_test_ai.pack(side="left", padx=4)
        self.lbl_ai_test_status = ctk.CTkLabel(btn_ai_row, text="", text_color=theme.TEXT_MUTED)
        self.lbl_ai_test_status.pack(side="left", padx=10)

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
            drag_handle.pack(side="left", padx=(8, 4))

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
                try:
                    self.root.after(35, self._flush_log_buffer)
                except Exception:
                    self._log_flush_pending = False

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
            messagebox.showinfo("Export", "Log is currently empty.", parent=self.root)
            return
        filename = f"log_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=filename)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Export", f"Log successfully exported to:\n{path}", parent=self.root)

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
            messagebox.showwarning("Login", "Please select or add an account first.", parent=self.root)
            return
        if not acc.password:
            messagebox.showwarning("Login", f"Account '{acc.username}' has no password configured. Passwordless accounts can only be used as copy targets.", parent=self.root)
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
                self.root.after(0, lambda: messagebox.showerror("Login failed", msg, parent=self.root))

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
                    self.root.after(0, lambda: messagebox.showerror("Login failed", f"Could not authenticate as '{acc.username}':\n\n{msg}", parent=self.root))

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Handlers: Coordinates Upload
    # --------------------------------------------------------------------------

    def _browse_coords_file(self):
        initial_dir = getattr(self.config, "last_browse_dir", "") or getattr(self.config, "folder_path", "") or os.path.expanduser("~")
        if not os.path.isdir(initial_dir):
            initial_dir = os.path.expanduser("~")

        files = filedialog.askopenfilenames(
            title="Select GPX / LOC file(s)",
            filetypes=[("GPX & LOC files", "*.gpx *.loc"), ("All files", "*.*")],
            initialdir=initial_dir
        )
        if files:
            chosen_dir = os.path.dirname(files[0])
            self.config.last_browse_dir = chosen_dir
            self.config.folder_path = chosen_dir
            self.config_mgr.save()

            self.selected_coords_files = list(files)
            self.coords_path_entry.delete(0, "end")
            if len(files) == 1:
                self.coords_path_entry.insert(0, files[0])
            else:
                summary = f"[{len(files)} files selected] {', '.join(os.path.basename(f) for f in files[:2])}{'...' if len(files) > 2 else ''}"
                self.coords_path_entry.insert(0, summary)

            self._load_coords_items()

    def _load_coords_from_path(self, path: str):
        if not path:
            return
        self.selected_coords_files = [path]
        self._load_coords_items()

    def _load_coords_items(self):
        files_to_load = getattr(self, "selected_coords_files", [])
        raw = self.coords_path_entry.get().strip()

        # If user manually typed/pasted a path into entry
        if not files_to_load or (raw and not raw.startswith("[") and (not files_to_load or raw != files_to_load[0])):
            if ";" in raw:
                files_to_load = [f.strip() for f in raw.split(";") if f.strip() and os.path.exists(f.strip())]
            elif raw and os.path.exists(raw):
                files_to_load = [raw]

        if not files_to_load:
            self.lbl_coords_summary.configure(text="No files selected or path not found.", text_color=theme.ACCENT_RED)
            return

        all_waypoints_map = {}
        loaded_sources = []
        for f_path in files_to_load:
            if not os.path.exists(f_path):
                continue
            loaded_sources.append(os.path.basename(f_path))
            if os.path.isfile(f_path):
                file_items = parse_file(f_path)
            elif os.path.isdir(f_path):
                file_items = parse_folder(f_path)
            else:
                continue

            for it in file_items:
                # If cache already present, prefer the one with modified coordinates hint
                if it.gccode not in all_waypoints_map or it.is_modified_hint:
                    all_waypoints_map[it.gccode] = it

        self.loaded_waypoints = list(all_waypoints_map.values())

        f_cfg = self.config.filters
        if f_cfg.has_any_enabled:
            orig_count = len(self.loaded_waypoints)
            self.loaded_waypoints = [
                w for w in self.loaded_waypoints
                if not is_cache_filtered(
                    w.cache_type,
                    omit_virtual=f_cfg.omit_virtual,
                    omit_earth=f_cfg.omit_earth,
                    omit_events=f_cfg.omit_events,
                    omit_webcam=f_cfg.omit_webcam,
                    omit_traditional=f_cfg.omit_traditional,
                    omit_other=f_cfg.omit_other
                )
            ]
            omitted = orig_count - len(self.loaded_waypoints)
            filter_msg = f" ({omitted} omitted by filter)" if omitted else ""
        else:
            filter_msg = ""

        count = len(self.loaded_waypoints)
        modified_hints = sum(1 for w in self.loaded_waypoints if w.is_modified_hint)
        notes_count = sum(1 for w in self.loaded_waypoints if getattr(w, "note", ""))
        src_label = loaded_sources[0] if len(loaded_sources) == 1 else f"{len(loaded_sources)} files"
        self.lbl_coords_summary.configure(
            text=f"Loaded {count} caches ({count} with coords, {notes_count} with notes){filter_msg}.",
            text_color=theme.ACCENT_GREEN if count > 0 else theme.TEXT_MUTED
        )
        self.log(f"Loaded {count} waypoints from {src_label} ({notes_count} with notes){filter_msg}.", "info")

    def _check_active_checkpoint(self):
        # 1. Coordinate upload run checkpoint
        if hasattr(self, "frame_resume_coords"):
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

        # 2. Bulk logging run checkpoint
        if hasattr(self, "frame_resume_logging"):
            if self.checkpoint_mgr.has_active_logging_run() and not self.is_running:
                log_state = self.checkpoint_mgr.load_active_logging_run()
                if log_state:
                    done = len(log_state.completed_gccodes)
                    self.lbl_resume_logging_info.configure(
                        text=f"⚠️ Unfinished bulk logging detected: {done}/{log_state.total} caches logged ({log_state.log_type_key})."
                    )
                    self.frame_resume_logging.pack(fill="x", pady=6, padx=6, before=self.btn_log_start.master)
            else:
                self.frame_resume_logging.pack_forget()

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
            messagebox.showwarning("Resume upload", f"Source file was moved or deleted:\n{state.file_path}\nPlease re-select the file.", parent=self.root)
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
        if messagebox.askyesno("Discard checkpoint", "Discard progress from the unfinished upload?", parent=self.root):
            self.checkpoint_mgr.discard_coords_run()
            self.frame_resume_coords.pack_forget()
            self.log("Discarded unfinished upload checkpoint.", "info")

    def _on_resume_logging_clicked(self):
        state = self.checkpoint_mgr.load_active_logging_run()
        if not state:
            self.frame_resume_logging.pack_forget()
            return

        self.txt_gc_codes.delete("1.0", "end")
        self.txt_gc_codes.insert("1.0", " ".join(state.all_gccodes))
        if state.date_str:
            self.entry_date.delete(0, "end")
            self.entry_date.insert(0, state.date_str)
        if state.log_text:
            self.txt_log_message.delete("1.0", "end")
            self.txt_log_message.insert("1.0", state.log_text)
        if state.log_type_key in LOG_TYPE_NAMES:
            self.combo_log_type.set(LOG_TYPE_NAMES[state.log_type_key])

        saved_logs = self.checkpoint_mgr.read_saved_logging_logs(25)
        if saved_logs:
            self.log("--- Restoring recent log from previous interrupted run ---", "info")
            for line in saved_logs:
                self.txt_console.insert("end", f"{line}\n")
            self.txt_console.see("end")

        done_count = len(state.completed_gccodes)
        self.log(f"Resuming bulk logging from checkpoint: {done_count}/{state.total} caches already completed.", "info")
        self._start_bulk_logging(resume=True)

    def _on_discard_logging_clicked(self):
        if messagebox.askyesno("Discard checkpoint", "Discard progress from the unfinished bulk logging?", parent=self.root):
            self.checkpoint_mgr.discard_logging_run()
            self.frame_resume_logging.pack_forget()
            self.log("Discarded unfinished bulk logging checkpoint.", "info")

    def _start_coords_upload(self, resume: bool = False):
        if not self.loaded_waypoints:
            messagebox.showwarning("Upload", "No waypoints loaded. Choose a GPX file first.", parent=self.root)
            return
        upload_coords = self.var_upload_coords.get()
        upload_notes = self.var_upload_notes.get()
        if not upload_coords and not upload_notes:
            messagebox.showwarning("Upload", "Please select at least one item to upload (Coordinates or Personal notes).", parent=self.root)
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
        uploader = CoordinateUploader(
            self.client,
            self.safety,
            checkpoint_manager=self.checkpoint_mgr,
            geodeep_config=self.config.geodeep_notes
        )

        def on_progress(idx, total, pct, eta, item):
            self.root.after(0, lambda: self._update_progress_ui(idx, total, pct, eta, f"Uploading {item.gccode}"))

        def worker():
            try:
                f_cfg = self.config.filters
                res = uploader.run(
                    self.loaded_waypoints,
                    mode=mode,
                    upload_coords=upload_coords,
                    upload_notes=upload_notes,
                    on_progress=on_progress,
                    on_log=self.log,
                    source_file_path=source_path,
                    resume=resume,
                    omit_virtual=f_cfg.omit_virtual,
                    omit_earth=f_cfg.omit_earth,
                    omit_events=f_cfg.omit_events,
                    omit_webcam=f_cfg.omit_webcam,
                    omit_traditional=f_cfg.omit_traditional,
                    omit_other=f_cfg.omit_other,
                    geodeep_config=self.config.geodeep_notes
                )
                self.root.after(0, self._reset_controls)
                self.root.after(0, lambda: self._on_operation_done("Coordinates upload", res))
            except Exception as e:
                self.log(f"Error during coordinate upload: {e}", "error")
                self.root.after(0, self._reset_controls)
                self.root.after(0, lambda: self._on_operation_error("Coordinates upload", str(e)))
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
        initial_dir = getattr(self.config, "last_browse_dir", None) or os.path.expanduser("~")
        files = filedialog.askopenfilenames(
            initialdir=initial_dir,
            filetypes=[("GPX & LOC files", "*.gpx *.loc"), ("Text files", "*.txt"), ("All files", "*.*")],
            parent=self.root
        )
        if files:
            self.config.last_browse_dir = os.path.dirname(files[0])
            self.config_mgr.save()
            all_codes = []
            total_omitted = 0
            f_cfg = self.config.filters
            for f in files:
                items = parse_file(f)
                orig_c = len(items)
                if f_cfg.has_any_enabled:
                    items = [
                        it for it in items
                        if not is_cache_filtered(
                            it.cache_type,
                            omit_virtual=f_cfg.omit_virtual,
                            omit_earth=f_cfg.omit_earth,
                            omit_events=f_cfg.omit_events,
                            omit_webcam=f_cfg.omit_webcam,
                            omit_traditional=f_cfg.omit_traditional,
                            omit_other=f_cfg.omit_other
                        )
                    ]
                    total_omitted += (orig_c - len(items))
                codes = [it.gccode for it in items if it.gccode]
                all_codes.extend(codes)

            if all_codes:
                self.txt_gc_codes.insert("end", " ".join(all_codes) + "\n")
                omitted_msg = f" ({total_omitted} omitted by filter)" if total_omitted else ""
                self.log(f"Imported {len(all_codes)} GC codes from {len(files)} file(s){omitted_msg}.", "info")

    def _open_gc_list_import_dialog(self, target: str = "bulk"):
        """
        Open dialog to import caches from a Geocaching Bookmark list (by URL, code, or user profile lists).
        target: 'bulk' (inserts to txt_gc_codes with type filtering) or 'ignore' (inserts to txt_ignore_codes).
        """
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Import caches from Geocaching List")
        dialog.geometry("540x360")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        try:
            x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 270
            y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 180
            dialog.geometry(f"+{x}+{y}")
        except Exception:
            pass

        pad_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        pad_frame.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(
            pad_frame,
            text="Import caches from Geocaching List",
            font=ctk.CTkFont(size=15, weight="bold")
        ).pack(anchor="w", pady=(0, 12))

        # 1. URL or Code Entry (TOP - FIRST with immediate focus)
        ctk.CTkLabel(
            pad_frame,
            text="URL or list code:",
            font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", pady=(4, 2))

        entry_list_ref = ctk.CTkEntry(
            pad_frame,
            placeholder_text="https://www.geocaching.com/plan/lists/BMDQMZP or BMDQMZP",
            height=34
        )
        entry_list_ref.pack(fill="x", pady=(0, 10))
        entry_list_ref.focus_set()

        # 2. Profile Lists Dropdown (SECOND - BELOW entry)
        ctk.CTkLabel(
            pad_frame,
            text="Or select from your profile lists:",
            font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", pady=(4, 2))

        list_row = ctk.CTkFrame(pad_frame, fg_color="transparent")
        list_row.pack(fill="x", pady=(0, 8))

        user_lists_data: List[Dict[str, Any]] = []
        combo_user_lists = ctk.CTkComboBox(
            list_row,
            values=["-- Select from profile lists --"],
            height=32,
            state="readonly"
        )
        combo_user_lists.pack(side="left", fill="x", expand=True, padx=(0, 6))

        def on_list_selected(choice: str):
            if not choice or choice.startswith("--"):
                return
            for item in user_lists_data:
                label = f"{item['name']} ({item['count']}) - {item['referenceCode']}"
                if choice == label:
                    entry_list_ref.delete(0, "end")
                    entry_list_ref.insert(0, item['referenceCode'])
                    break

        combo_user_lists.configure(command=on_list_selected)

        btn_refresh = ctk.CTkButton(list_row, text="Refresh", width=75, height=32)
        btn_refresh.pack(side="left")

        # Optional checkbox for emptying list on ignore
        if target == "ignore":
            chk_empty = ctk.CTkCheckBox(
                pad_frame,
                text="Empty list after ignoring (remove items from list)",
                font=ctk.CTkFont(size=12),
                variable=self.var_empty_source_list
            )
            chk_empty.pack(anchor="w", pady=(4, 4))

        # Status label
        lbl_status = ctk.CTkLabel(
            pad_frame,
            text="Ready",
            text_color=theme.TEXT_MUTED,
            font=ctk.CTkFont(size=12)
        )
        lbl_status.pack(anchor="w", pady=(4, 8))

        def load_lists_worker():
            try:
                lists = self.client.get_user_lists()
                user_lists_data.clear()
                user_lists_data.extend(lists)
                if lists:
                    selected_val = "-- Select from profile lists --"
                    if target == "ignore":
                        ignore_lists = [it for it in lists if "ignore" in it.get("name", "").lower()]
                        other_lists = [it for it in lists if "ignore" not in it.get("name", "").lower()]
                        lists = ignore_lists + other_lists
                        user_lists_data.clear()
                        user_lists_data.extend(lists)
                        if ignore_lists:
                            first_ignore = ignore_lists[0]
                            selected_val = f"{first_ignore['name']} ({first_ignore['count']}) - {first_ignore['referenceCode']}"

                    display_vals = ["-- Select from profile lists --"] + [
                        f"{it['name']} ({it['count']}) - {it['referenceCode']}" for it in lists
                    ]
                    def update_combo():
                        combo_user_lists.configure(values=display_vals)
                        combo_user_lists.set(selected_val)
                        if selected_val != "-- Select from profile lists --":
                            for item in user_lists_data:
                                label = f"{item['name']} ({item['count']}) - {item['referenceCode']}"
                                if selected_val == label:
                                    entry_list_ref.delete(0, "end")
                                    entry_list_ref.insert(0, item['referenceCode'])
                                    lbl_status.configure(text=f"Loaded {len(lists)} list(s). Pre-selected '{item['name']}'.", text_color=theme.TEXT_MUTED)
                                    break
                        else:
                            lbl_status.configure(text=f"Loaded {len(lists)} list(s) from profile.", text_color=theme.TEXT_MUTED)
                        btn_refresh.configure(state="normal")
                    dialog.after(0, update_combo)
                else:
                    def update_empty():
                        lbl_status.configure(text="No lists found or not logged in.", text_color=theme.TEXT_MUTED)
                        btn_refresh.configure(state="normal")
                    dialog.after(0, update_empty)
            except Exception as e:
                def update_err():
                    lbl_status.configure(text=f"Error loading lists: {e}", text_color=theme.TEXT_MUTED)
                    btn_refresh.configure(state="normal")
                dialog.after(0, update_err)

        def trigger_refresh():
            btn_refresh.configure(state="disabled")
            lbl_status.configure(text="Loading lists from profile...", text_color=theme.TEXT_MUTED)
            threading.Thread(target=load_lists_worker, daemon=True).start()

        btn_refresh.configure(command=trigger_refresh)

        # Action buttons
        btn_box = ctk.CTkFrame(pad_frame, fg_color="transparent")
        btn_box.pack(fill="x", side="bottom", pady=(8, 0))

        def do_fetch():
            raw_input = entry_list_ref.get().strip()
            ref_code = self.client.extract_list_reference_code(raw_input)
            if not ref_code:
                lbl_status.configure(text="Please enter a valid list URL or code (e.g. BMDQMZP).", text_color=theme.ACCENT_RED)
                return

            btn_fetch.configure(state="disabled")
            btn_cancel.configure(state="disabled")
            lbl_status.configure(text=f"Fetching geocaches from list {ref_code}...", text_color=theme.ACCENT_BLUE)

            def fetch_worker():
                try:
                    caches, list_name = self.client.get_list_geocaches(ref_code)
                    if not caches:
                        def on_empty():
                            lbl_status.configure(text=f"No caches found in list {ref_code}.", text_color=theme.ACCENT_RED)
                            btn_fetch.configure(state="normal")
                            btn_cancel.configure(state="normal")
                        dialog.after(0, on_empty)
                        return

                    f_cfg = self.config.filters
                    total_count = len(caches)
                    omitted = 0

                    if target == "bulk":
                        # Filter by active types
                        if f_cfg.has_any_enabled:
                            filtered_caches = []
                            for c in caches:
                                c_type = c.get("geocacheType")
                                t_name = c_type.get("name") if isinstance(c_type, dict) else c_type
                                t_id = c_type.get("id") if isinstance(c_type, dict) else None
                                is_filt = is_cache_filtered(
                                    t_name,
                                    omit_virtual=f_cfg.omit_virtual,
                                    omit_earth=f_cfg.omit_earth,
                                    omit_events=f_cfg.omit_events,
                                    omit_webcam=f_cfg.omit_webcam,
                                    omit_traditional=f_cfg.omit_traditional,
                                    omit_other=f_cfg.omit_other
                                )
                                if not is_filt and t_id is not None:
                                    is_filt = is_cache_filtered(
                                        t_id,
                                        omit_virtual=f_cfg.omit_virtual,
                                        omit_earth=f_cfg.omit_earth,
                                        omit_events=f_cfg.omit_events,
                                        omit_webcam=f_cfg.omit_webcam,
                                        omit_traditional=f_cfg.omit_traditional,
                                        omit_other=f_cfg.omit_other
                                    )
                                if not is_filt:
                                    filtered_caches.append(c)

                            omitted = total_count - len(filtered_caches)
                            caches = filtered_caches

                        codes = [c["referenceCode"] for c in caches if c.get("referenceCode")]
                        def apply_bulk():
                            if codes:
                                self.txt_gc_codes.insert("end", " ".join(codes) + "\n")
                                list_disp = f" '{list_name}'" if list_name else f" {ref_code}"
                                omit_str = f" ({omitted} omitted by filter)" if omitted else ""
                                self.log(f"Imported {len(codes)} GC codes from list{list_disp}{omit_str}.", "info")
                            dialog.destroy()
                        dialog.after(0, apply_bulk)
                    else:
                        # Target: ignore list
                        codes = [c["referenceCode"] for c in caches if c.get("referenceCode")]
                        def apply_ignore():
                            if codes:
                                self.ignore_source_list_code = ref_code
                                self.txt_ignore_codes.insert("end", " ".join(codes) + "\n")
                                list_disp = f" '{list_name}'" if list_name else f" {ref_code}"
                                empty_msg = " (will empty from list on ignore)" if self.var_empty_source_list.get() else ""
                                self.log(f"Imported {len(codes)} GC codes from list{list_disp}{empty_msg}.", "info")
                            dialog.destroy()
                        dialog.after(0, apply_ignore)

                except Exception as e:
                    def on_err():
                        lbl_status.configure(text=f"Error fetching caches: {e}", text_color=theme.ACCENT_RED)
                        btn_fetch.configure(state="normal")
                        btn_cancel.configure(state="normal")
                    dialog.after(0, on_err)

            threading.Thread(target=fetch_worker, daemon=True).start()

        btn_fetch = ctk.CTkButton(
            btn_box,
            text="Fetch caches",
            font=ctk.CTkFont(weight="bold"),
            fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            height=34,
            command=do_fetch
        )
        btn_fetch.pack(side="left", padx=(0, 6))

        btn_cancel = ctk.CTkButton(
            btn_box,
            text="Cancel",
            width=90,
            height=34,
            fg_color=theme.CARD_BORDER,
            command=dialog.destroy
        )
        btn_cancel.pack(side="left")

        # Trigger initial background load of profile lists
        trigger_refresh()

    def _start_bulk_logging(self, resume: bool = False):
        if self.is_running:
            return

        mode = self.logger_mode_var.get() if hasattr(self, "logger_mode_var") else "CREATE"

        # 0. CONCEPTS Mode (Send concepts)
        if mode == "CONCEPTS":
            self.is_running = True
            self.btn_log_start.configure(state="disabled")
            self.btn_log_pause.configure(state="normal", text="⏸ Pause")
            self.btn_log_stop.configure(state="normal")
            self.progress_bar.set(0)

            logger = CacheLogger(self.client, self.safety)

            def on_progress_concepts(idx, total, pct, eta, gc_code):
                self.root.after(0, lambda: self._update_progress_ui(idx, total, pct, eta, f"Sending concept {gc_code}"))

            def worker_concepts():
                try:
                    res = logger.run_send_concepts(
                        on_progress=on_progress_concepts,
                        on_log=self.log,
                        send_answers=self.var_send_answers.get(),
                        answer_templates=self.config.answer_templates,
                        selected_template_name=self.combo_answer_templates.get() if hasattr(self, "combo_answer_templates") else "Default",
                        ai_config=self.config.ai,
                        default_answer_source=self.config.default_answer_source,
                        on_ask_answer=self._on_worker_ask_answer
                    )
                    self.root.after(0, self._reset_controls)
                    self.root.after(0, lambda: self._on_operation_done("Send concepts", res))
                except Exception as e:
                    self.log(f"Error during sending concepts: {e}", "error")
                    self.root.after(0, self._reset_controls)
                    self.root.after(0, lambda: self._on_operation_error("Send concepts", str(e)))
                finally:
                    self.root.after(0, self._reset_controls)
                    self.root.after(0, self._reset_approval_panel_to_idle)

            threading.Thread(target=worker_concepts, daemon=True).start()
            return

        raw_text = self.txt_gc_codes.get("1.0", "end").strip()
        codes = extract_gc_codes_from_string(raw_text)
        if not codes:
            messagebox.showwarning("Log caches", "No valid GC codes found. Please paste GC codes or import a GPX.", parent=self.root)
            return

        # 1. DELETE Mode
        if mode == "DELETE":
            if not messagebox.askyesno(
                "Confirm delete logs",
                f"Are you sure you want to permanently delete your log(s) for {len(codes)} cache(s)?\n\nThis cannot be undone. Continue?",
                parent=self.root
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
                    self.root.after(0, self._reset_controls)
                    self.root.after(0, lambda: self._on_operation_done("Bulk delete logs", res))
                except Exception as e:
                    self.log(f"Error during bulk delete logs: {e}", "error")
                    self.root.after(0, self._reset_controls)
                    self.root.after(0, lambda: self._on_operation_error("Bulk delete logs", str(e)))
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
                    self.root.after(0, self._reset_controls)
                    self.root.after(0, lambda: self._on_operation_done("Bulk edit logs", res))
                except Exception as e:
                    self.log(f"Error during bulk edit logs: {e}", "error")
                    self.root.after(0, self._reset_controls)
                    self.root.after(0, lambda: self._on_operation_error("Bulk edit logs", str(e)))
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
            if not messagebox.askyesno("Warning", "You selected 'Needs archive'. This alerts reviewers to ALL listed caches. Continue?", parent=self.root):
                return

        date_str = self._parse_to_iso_date(self.entry_date.get())
        log_text = self.txt_log_message.get("1.0", "end").strip()

        if hasattr(self, "frame_resume_logging"):
            self.frame_resume_logging.pack_forget()

        self.is_running = True
        self.btn_log_start.configure(state="disabled")
        self.btn_log_pause.configure(state="normal", text="⏸ Pause")
        self.btn_log_stop.configure(state="normal")
        self.progress_bar.set(0)

        logger = CacheLogger(self.client, self.safety)

        def on_progress(idx, total, pct, eta, gc_code):
            self.root.after(0, lambda: self._update_progress_ui(idx, total, pct, eta, f"Logging {gc_code}"))

        def worker():
            try:
                f_cfg = self.config.filters
                res = logger.run_log(
                    codes,
                    log_type_key=type_key,
                    date_str=date_str,
                    log_text=log_text,
                    skip_already_found=True,
                    omit_virtual=f_cfg.omit_virtual,
                    omit_earth=f_cfg.omit_earth,
                    omit_events=f_cfg.omit_events,
                    omit_webcam=f_cfg.omit_webcam,
                    omit_traditional=f_cfg.omit_traditional,
                    omit_other=f_cfg.omit_other,
                    checkpoint_manager=self.checkpoint_mgr,
                    resume=resume,
                    on_progress=on_progress,
                    on_log=self.log
                )
                self.root.after(0, self._reset_controls)
                self.root.after(0, lambda: self._on_operation_done("Bulk logging", res))
            except Exception as e:
                self.log(f"Error during bulk logging: {e}", "error")
                self.root.after(0, self._reset_controls)
                self.root.after(0, lambda: self._on_operation_error("Bulk logging", str(e)))
            finally:
                self.root.after(0, self._reset_controls)
                self.root.after(0, self._check_active_checkpoint)

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Handlers: Copy User & Ignore
    # --------------------------------------------------------------------------

    def _start_copy_user_fetch(self):
        target_user = self.entry_copy_user.get().strip()
        target_pwd = self.entry_copy_pwd.get().strip() if self.var_password_mode.get() else None

        if not target_user:
            messagebox.showwarning("Copy user", "Enter a target username.", parent=self.root)
            return

        if self.var_password_mode.get() and not target_pwd:
            messagebox.showwarning("Copy user", "Password mode is enabled, but no password was entered.\nPlease enter a password or uncheck Password mode.", parent=self.root)
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
            f_cfg = self.config.filters
            codes = copier.fetch_user_logged_caches(
                target_user,
                target_pwd,
                target_date,
                on_log=self.log,
                end_date_str=end_date,
                omit_virtual=f_cfg.omit_virtual,
                omit_earth=f_cfg.omit_earth,
                omit_events=f_cfg.omit_events,
                omit_webcam=f_cfg.omit_webcam,
                omit_traditional=f_cfg.omit_traditional,
                omit_other=f_cfg.omit_other
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
        initial_dir = getattr(self.config, "last_browse_dir", "") or getattr(self.config, "folder_path", "") or os.path.expanduser("~")
        if not os.path.isdir(initial_dir):
            initial_dir = os.path.expanduser("~")

        files = filedialog.askopenfilenames(
            title="Select GPX / LOC file(s) to import",
            filetypes=[("GPX & LOC files", "*.gpx *.loc"), ("All files", "*.*")],
            initialdir=initial_dir
        )
        if files:
            chosen_dir = os.path.dirname(files[0])
            self.config.last_browse_dir = chosen_dir
            self.config.folder_path = chosen_dir
            self.config_mgr.save()

            all_codes = []
            for path in files:
                if os.path.isfile(path):
                    items = parse_file(path)
                    all_codes.extend([it.gccode for it in items if it.gccode])
                elif os.path.isdir(path):
                    items = parse_folder(path)
                    all_codes.extend([it.gccode for it in items if it.gccode])

            # Deduplicate while preserving order
            seen = set()
            unique_codes = [c for c in all_codes if not (c in seen or seen.add(c))]

            if unique_codes:
                self.txt_ignore_codes.insert("end", " ".join(unique_codes) + "\n")
                self.log(f"Imported {len(unique_codes)} unique GC codes from {len(files)} file(s).", "info")
            else:
                messagebox.showinfo("Import", "No GC codes found in selected file(s).", parent=self.root)

    def _insert_copied_codes(self, codes: List[str]):
        self.tabview.set("📝 Bulk log caches")
        self.txt_gc_codes.delete("1.0", "end")
        self.txt_gc_codes.insert("1.0", " ".join(codes))
        messagebox.showinfo("Caches loaded", f"Loaded {len(codes)} caches into Bulk Logging tab!", parent=self.root)

    def _start_bulk_ignore(self):
        if self.is_running:
            return
        raw = self.txt_ignore_codes.get("1.0", "end").strip()
        codes = extract_gc_codes_from_string(raw)
        if not codes:
            messagebox.showwarning("Ignore", "No GC codes found to ignore.", parent=self.root)
            return

        remove_from_list = self.var_empty_source_list.get()
        source_list = self.ignore_source_list_code
        confirm_extra = f"\n(Items will also be removed from list {source_list})" if remove_from_list and source_list else ""
        if not messagebox.askyesno("Confirm", f"Add {len(codes)} caches to Ignore list?{confirm_extra}", parent=self.root):
            return

        self.is_running = True
        logger = CacheLogger(self.client, self.safety)

        def worker():
            try:
                res = logger.run_ignore(
                    codes,
                    on_log=self.log,
                    source_list_code=source_list,
                    remove_from_source_list=remove_from_list
                )
                self.root.after(0, self._reset_controls)
                self.root.after(0, lambda: self._on_operation_done("Ignore list", res))
            except Exception as e:
                self.log(f"Error during ignore update: {e}", "error")
                self.root.after(0, self._reset_controls)
                self.root.after(0, lambda: self._on_operation_error("Ignore list", str(e)))
            finally:
                self.root.after(0, self._reset_controls)

        threading.Thread(target=worker, daemon=True).start()

    def _start_bulk_unignore(self):
        if self.is_running:
            return
        raw = self.txt_ignore_codes.get("1.0", "end").strip()
        codes = extract_gc_codes_from_string(raw)
        if not codes:
            messagebox.showwarning("Un-ignore", "No GC codes found to un-ignore.", parent=self.root)
            return

        if not messagebox.askyesno("Confirm", f"Remove {len(codes)} caches from Ignore list (un-ignore)?", parent=self.root):
            return

        self.is_running = True
        logger = CacheLogger(self.client, self.safety)

        def worker():
            try:
                res = logger.run_unignore(codes, on_log=self.log)
                self.root.after(0, self._reset_controls)
                self.root.after(0, lambda: self._on_operation_done("Un-ignore list", res))
            except Exception as e:
                self.log(f"Error during un-ignore update: {e}", "error")
                self.root.after(0, self._reset_controls)
                self.root.after(0, lambda: self._on_operation_error("Un-ignore list", str(e)))
            finally:
                self.root.after(0, self._reset_controls)

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_ignored_caches(self):
        if not self.client.ensure_valid_session():
            messagebox.showwarning("Session invalid", "Please log in first to fetch ignored caches.", parent=self.root)
            return

        self.log("Fetching ignored cache list from Geocaching.com...", "info")

        def worker():
            try:
                codes = self.client.get_ignored_cache_codes()
                if codes:
                    def update_ui():
                        self.txt_ignore_codes.delete("1.0", "end")
                        self.txt_ignore_codes.insert("1.0", " ".join(sorted(codes)))
                        self.log(f"Loaded {len(codes)} ignored cache(s) into editor.", "success")
                    self.root.after(0, update_ui)
                else:
                    self.root.after(0, lambda: self.log("No ignored caches found on your account.", "warning"))
            except Exception as e:
                self.root.after(0, lambda: self.log(f"Error fetching ignored caches: {e}", "error"))

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Settings / Config Tab Handlers
    # --------------------------------------------------------------------------

    def _on_add_account(self):
        u = self.new_acc_user.get().strip()
        p = self.new_acc_pwd.get().strip()
        d = bool(self.new_acc_default.get())
        if not u:
            messagebox.showwarning("Account", "Username is required.", parent=self.root)
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
        if messagebox.askyesno("Delete account", "Remove this account from configuration?", parent=self.root):
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
            drag_handle.pack(side="left", padx=(8, 4))

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
            messagebox.showwarning("Log template", "Template text cannot be empty.", parent=self.root)
            return
        if t in self.config.log_templates:
            messagebox.showwarning("Log template", "This template already exists.", parent=self.root)
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
            if messagebox.askyesno("Delete template", f"Remove template #{index + 1}?\n\"{display_snip}\"", parent=self.root):
                self.config.log_templates.pop(index)
                if not self.config.log_templates:
                    self.config.log_templates = ["TFTC."]
                self.config_mgr.save()
                self._refresh_templates_view()
                self._update_combo_template_values()
                self.log(f"Removed template #{index + 1}.", "info")

    # --------------------------------------------------------------------------
    # Answer Templates Management
    # --------------------------------------------------------------------------

    def _refresh_answer_templates_view(self):
        if not hasattr(self, "ans_tmpl_display_frame"):
            return
        for widget in self.ans_tmpl_display_frame.winfo_children():
            widget.destroy()

        self._ans_tmpl_rows = []

        if not self.config.answer_templates:
            ctk.CTkLabel(self.ans_tmpl_display_frame, text="No answer templates configured. Add one below.", text_color=theme.TEXT_MUTED).pack(anchor="w", pady=4)
            return

        for idx, tmpl in enumerate(self.config.answer_templates):
            row = ctk.CTkFrame(self.ans_tmpl_display_frame, fg_color=theme.CARD_BG, corner_radius=6, border_width=1, border_color=theme.CARD_BORDER)
            row.pack(fill="x", pady=2)
            self._ans_tmpl_rows.append((row, idx))

            # Drag handle
            drag_handle = ctk.CTkLabel(row, text=" ☰ ", font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.TEXT_MUTED, cursor="fleur")
            drag_handle.pack(side="left", padx=(8, 4))

            display_snip = tmpl.text.replace("\n", " ").strip()
            if len(display_snip) > 42:
                display_snip = display_snip[:39] + "..."

            lbl_name = ctk.CTkLabel(row, text=f"#{idx + 1}: {tmpl.name}", font=ctk.CTkFont(weight="bold"))
            lbl_name.pack(side="left", padx=(4, 4), pady=6)

            if getattr(tmpl, "ai_prompt", ""):
                lbl_badge = ctk.CTkLabel(row, text=f"[{tmpl.ai_prompt}]", font=ctk.CTkFont(size=11, weight="bold"), text_color=theme.ACCENT_BLUE)
                lbl_badge.pack(side="left", padx=4, pady=6)

            lbl_preview = ctk.CTkLabel(row, text=f"({display_snip})", font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED)
            lbl_preview.pack(side="left", padx=4, pady=6)

            # Bind drag & drop
            for w in (drag_handle, lbl_name):
                w.bind("<Button-1>", lambda e, i=idx: self._on_ans_tmpl_drag_start(e, i))
                w.bind("<B1-Motion>", self._on_ans_tmpl_drag_motion)
                w.bind("<ButtonRelease-1>", self._on_ans_tmpl_drag_release)

            del_btn = ctk.CTkButton(
                row,
                text="Delete",
                width=65,
                height=26,
                fg_color=theme.ACCENT_RED,
                hover_color=theme.ACCENT_RED_HOVER,
                command=lambda t_idx=idx: self._on_delete_answer_template(t_idx)
            )
            del_btn.pack(side="right", padx=4)

            edit_btn = ctk.CTkButton(
                row,
                text="Edit",
                width=55,
                height=26,
                fg_color=theme.ACCENT_BLUE,
                hover_color=theme.ACCENT_BLUE_HOVER,
                command=lambda t_idx=idx: self._on_edit_answer_template(t_idx)
            )
            edit_btn.pack(side="right", padx=4)

    def _on_ans_tmpl_drag_start(self, event, idx: int):
        self._drag_ans_tmpl_idx = idx
        if 0 <= idx < len(self._ans_tmpl_rows):
            row_frame, _ = self._ans_tmpl_rows[idx]
            row_frame.configure(border_color=theme.ACCENT_BLUE, border_width=2)

    def _on_ans_tmpl_drag_motion(self, event):
        if self._drag_ans_tmpl_idx is None:
            return
        target_idx = self._find_ans_tmpl_drop_target(event.y_root)
        for row_frame, i in self._ans_tmpl_rows:
            if i == self._drag_ans_tmpl_idx:
                row_frame.configure(border_color=theme.ACCENT_BLUE, border_width=2)
            elif i == target_idx:
                row_frame.configure(border_color=theme.ACCENT_GREEN, border_width=2)
            else:
                row_frame.configure(border_color=theme.CARD_BORDER, border_width=1)

    def _find_ans_tmpl_drop_target(self, y_root: int) -> Optional[int]:
        for row_frame, idx in self._ans_tmpl_rows:
            ry = row_frame.winfo_rooty()
            rh = row_frame.winfo_height()
            if ry <= y_root <= ry + rh:
                return idx
        return None

    def _on_ans_tmpl_drag_release(self, event):
        if self._drag_ans_tmpl_idx is None:
            return
        src_idx = self._drag_ans_tmpl_idx
        self._drag_ans_tmpl_idx = None
        target_idx = self._find_ans_tmpl_drop_target(event.y_root)

        for row_frame, _ in self._ans_tmpl_rows:
            try:
                row_frame.configure(border_color=theme.CARD_BORDER, border_width=1)
            except Exception:
                pass

        if target_idx is not None and target_idx != src_idx and 0 <= target_idx < len(self.config.answer_templates):
            tmpl = self.config.answer_templates.pop(src_idx)
            self.config.answer_templates.insert(target_idx, tmpl)
            self.config_mgr.save()
            self._refresh_answer_templates_view()
            self._update_combo_answer_templates_values()
            self.log(f"Moved answer template '{tmpl.name}' to position #{target_idx + 1}.", "info")

    def _update_combo_answer_templates_values(self):
        names = [t.name for t in self.config.answer_templates] or ["Default"]
        if hasattr(self, "combo_answer_templates"):
            self.combo_answer_templates.configure(values=names)
            cur = self.combo_answer_templates.get()
            if cur not in names:
                self.combo_answer_templates.set(names[0] if names else "Default")

    def _on_add_answer_template(self):
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Add Answer Template")
        dialog.geometry("520x440")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_toplevel(dialog, 520, 440)

        ctk.CTkLabel(dialog, text="Add Answer Template", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(14, 8))

        ctk.CTkLabel(dialog, text="Template Name (e.g. Solo, S partou, Deutsch):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(4, 2))
        entry_name = ctk.CTkEntry(dialog, placeholder_text="e.g. S partou")
        entry_name.pack(fill="x", padx=20, pady=(0, 6))

        ctk.CTkLabel(dialog, text="Assigned AI System Prompt (optional):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(4, 2))
        sp_names = ["(Default / First prompt)"] + [sp.name for sp in self.config.ai.system_prompts]
        combo_ai_prompt = ctk.CTkComboBox(dialog, values=sp_names, width=280)
        combo_ai_prompt.set(sp_names[0])
        combo_ai_prompt.pack(anchor="w", padx=20, pady=(0, 6))

        ctk.CTkLabel(dialog, text="Template Text (use [...] for answers, {username} for username):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(4, 2))
        txt_tmpl = ctk.CTkTextbox(dialog, height=140, wrap="word")
        txt_tmpl.pack(fill="both", expand=True, padx=20, pady=2)
        default_tmpl_text = "Ahoj, zasílám odpovědi na otázky z listingu:\n[...]\n\nDíky za keš!\n{username}"
        txt_tmpl.insert("1.0", default_tmpl_text)

        btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_box.pack(pady=12)

        def save():
            name = entry_name.get().strip()
            text = txt_tmpl.get("1.0", "end").strip()
            if not name:
                messagebox.showwarning("Answer Template", "Template name cannot be empty.", parent=dialog)
                return
            if not text:
                messagebox.showwarning("Answer Template", "Template text cannot be empty.", parent=dialog)
                return
            if any(t.name.lower() == name.lower() for t in self.config.answer_templates):
                messagebox.showwarning("Answer Template", f"A template named '{name}' already exists.", parent=dialog)
                return

            if "[...]" not in text and "{answers}" not in text:
                text = f"{text}\n\n[...]"

            sel_sp = combo_ai_prompt.get().strip()
            assigned_sp = "" if sel_sp.startswith("(") else sel_sp

            new_tmpl = AnswerTemplate(name=name, text=text, ai_prompt=assigned_sp)
            self.config.answer_templates.append(new_tmpl)
            self.config_mgr.save()
            self._refresh_answer_templates_view()
            self._update_combo_answer_templates_values()
            self.log(f"Added answer template '{name}'.", "success")
            dialog.destroy()

        ctk.CTkButton(btn_box, text="Save", width=100, fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_HOVER, command=save).pack(side="left", padx=6)
        ctk.CTkButton(btn_box, text="Cancel", width=80, fg_color=theme.CARD_BORDER, command=dialog.destroy).pack(side="left", padx=6)

    def _on_edit_answer_template(self, index: int):
        if not (0 <= index < len(self.config.answer_templates)):
            return

        tmpl = self.config.answer_templates[index]

        dialog = ctk.CTkToplevel(self.root)
        dialog.title(f"Edit Answer Template #{index + 1}")
        dialog.geometry("520x440")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_toplevel(dialog, 520, 440)

        ctk.CTkLabel(dialog, text=f"Edit Template '{tmpl.name}'", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(14, 8))

        ctk.CTkLabel(dialog, text="Template Name:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(4, 2))
        entry_name = ctk.CTkEntry(dialog)
        entry_name.insert(0, tmpl.name)
        entry_name.pack(fill="x", padx=20, pady=(0, 6))

        ctk.CTkLabel(dialog, text="Assigned AI System Prompt (optional):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(4, 2))
        sp_names = ["(Default / First prompt)"] + [sp.name for sp in self.config.ai.system_prompts]
        combo_ai_prompt = ctk.CTkComboBox(dialog, values=sp_names, width=280)
        curr_p = tmpl.ai_prompt if tmpl.ai_prompt in [sp.name for sp in self.config.ai.system_prompts] else sp_names[0]
        combo_ai_prompt.set(curr_p)
        combo_ai_prompt.pack(anchor="w", padx=20, pady=(0, 6))

        ctk.CTkLabel(dialog, text="Template Text (use [...] for answers, {username} for username):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(4, 2))
        txt_tmpl = ctk.CTkTextbox(dialog, height=140, wrap="word")
        txt_tmpl.pack(fill="both", expand=True, padx=20, pady=2)
        txt_tmpl.insert("1.0", tmpl.text)

        btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_box.pack(pady=12)

        def save():
            name = entry_name.get().strip()
            text = txt_tmpl.get("1.0", "end").strip()
            if not name:
                messagebox.showwarning("Answer Template", "Template name cannot be empty.", parent=dialog)
                return
            if not text:
                messagebox.showwarning("Answer Template", "Template text cannot be empty.", parent=dialog)
                return

            if "[...]" not in text and "{answers}" not in text:
                text = f"{text}\n\n[...]"

            sel_sp = combo_ai_prompt.get().strip()
            assigned_sp = "" if sel_sp.startswith("(") else sel_sp

            self.config.answer_templates[index].name = name
            self.config.answer_templates[index].text = text
            self.config.answer_templates[index].ai_prompt = assigned_sp
            self.config_mgr.save()
            self._refresh_answer_templates_view()
            self._update_combo_answer_templates_values()
            self._update_approval_preview()
            self.log(f"Updated answer template '{name}'.", "success")
            dialog.destroy()

        ctk.CTkButton(btn_box, text="Save", width=100, fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_HOVER, command=save).pack(side="left", padx=6)
        ctk.CTkButton(btn_box, text="Cancel", width=80, fg_color=theme.CARD_BORDER, command=dialog.destroy).pack(side="left", padx=6)

    def _on_delete_answer_template(self, index: int):
        if 0 <= index < len(self.config.answer_templates):
            if len(self.config.answer_templates) <= 1:
                messagebox.showwarning("Answer Template", "You must keep at least one answer template configured.", parent=self.root)
                return
            tmpl = self.config.answer_templates[index]
            if messagebox.askyesno("Delete template", f"Remove answer template '{tmpl.name}'?", parent=self.root):
                self.config.answer_templates.pop(index)
                self.config_mgr.save()
                self._refresh_answer_templates_view()
                self._update_combo_answer_templates_values()
                self._update_approval_preview()
                self.log(f"Removed answer template '{tmpl.name}'.", "info")

    # --------------------------------------------------------------------------
    # AI System Prompts Management
    # --------------------------------------------------------------------------

    def _refresh_sys_prompts_view(self):
        if not hasattr(self, "sys_prompt_display_frame"):
            return
        for widget in self.sys_prompt_display_frame.winfo_children():
            widget.destroy()

        self._sys_prompt_rows = []

        if not self.config.ai.system_prompts:
            ctk.CTkLabel(self.sys_prompt_display_frame, text="No system prompts configured. Click 'Reset to defaults'.", text_color=theme.TEXT_MUTED).pack(anchor="w", pady=4)
            return

        for idx, sp in enumerate(self.config.ai.system_prompts):
            row = ctk.CTkFrame(self.sys_prompt_display_frame, fg_color=theme.CARD_BG, corner_radius=6, border_width=1, border_color=theme.CARD_BORDER)
            row.pack(fill="x", pady=2)
            self._sys_prompt_rows.append((row, idx))

            # Drag handle
            drag_handle = ctk.CTkLabel(row, text=" ☰ ", font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.TEXT_MUTED, cursor="fleur")
            drag_handle.pack(side="left", padx=(8, 4))

            display_snip = sp.prompt.replace("\n", " ").strip()
            if len(display_snip) > 55:
                display_snip = display_snip[:52] + "..."

            lbl_name = ctk.CTkLabel(row, text=f"#{idx + 1}: {sp.name}", font=ctk.CTkFont(weight="bold"))
            lbl_name.pack(side="left", padx=(4, 4), pady=6)

            lbl_preview = ctk.CTkLabel(row, text=f"({display_snip})", font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED)
            lbl_preview.pack(side="left", padx=4, pady=6)

            for w in (drag_handle, lbl_name):
                w.bind("<Button-1>", lambda e, i=idx: self._on_sys_prompt_drag_start(e, i))
                w.bind("<B1-Motion>", self._on_sys_prompt_drag_motion)
                w.bind("<ButtonRelease-1>", self._on_sys_prompt_drag_release)

            del_btn = ctk.CTkButton(
                row,
                text="Delete",
                width=65,
                height=26,
                fg_color=theme.ACCENT_RED,
                hover_color=theme.ACCENT_RED_HOVER,
                command=lambda p_idx=idx: self._on_delete_system_prompt(p_idx)
            )
            del_btn.pack(side="right", padx=4)

            edit_btn = ctk.CTkButton(
                row,
                text="Edit",
                width=55,
                height=26,
                fg_color=theme.ACCENT_BLUE,
                hover_color=theme.ACCENT_BLUE_HOVER,
                command=lambda p_idx=idx: self._on_edit_system_prompt(p_idx)
            )
            edit_btn.pack(side="right", padx=4)

    def _on_sys_prompt_drag_start(self, event, idx: int):
        self._drag_sys_prompt_idx = idx
        if 0 <= idx < len(self._sys_prompt_rows):
            row_frame, _ = self._sys_prompt_rows[idx]
            row_frame.configure(border_color=theme.ACCENT_BLUE, border_width=2)

    def _on_sys_prompt_drag_motion(self, event):
        if self._drag_sys_prompt_idx is None:
            return
        target_idx = self._find_sys_prompt_drop_target(event.y_root)
        for row_frame, i in self._sys_prompt_rows:
            if i == self._drag_sys_prompt_idx:
                row_frame.configure(border_color=theme.ACCENT_BLUE, border_width=2)
            elif i == target_idx:
                row_frame.configure(border_color=theme.ACCENT_GREEN, border_width=2)
            else:
                row_frame.configure(border_color=theme.CARD_BORDER, border_width=1)

    def _find_sys_prompt_drop_target(self, y_root: int) -> Optional[int]:
        for row_frame, idx in self._sys_prompt_rows:
            ry = row_frame.winfo_rooty()
            rh = row_frame.winfo_height()
            if ry <= y_root <= ry + rh:
                return idx
        return None

    def _on_sys_prompt_drag_release(self, event):
        if self._drag_sys_prompt_idx is None:
            return
        src_idx = self._drag_sys_prompt_idx
        self._drag_sys_prompt_idx = None
        target_idx = self._find_sys_prompt_drop_target(event.y_root)

        for row_frame, _ in self._sys_prompt_rows:
            try:
                row_frame.configure(border_color=theme.CARD_BORDER, border_width=1)
            except Exception:
                pass

        if target_idx is not None and target_idx != src_idx and 0 <= target_idx < len(self.config.ai.system_prompts):
            sp = self.config.ai.system_prompts.pop(src_idx)
            self.config.ai.system_prompts.insert(target_idx, sp)
            self.config_mgr.save()
            self._refresh_sys_prompts_view()
            self.log(f"Moved system prompt '{sp.name}' to position #{target_idx + 1}.", "info")

    def _on_add_system_prompt(self):
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Add AI System Prompt")
        dialog.geometry("540x440")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_toplevel(dialog, 540, 440)

        ctk.CTkLabel(dialog, text="Add AI System Prompt", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(14, 8))

        ctk.CTkLabel(dialog, text="Prompt Name (e.g. 🇨🇿 Čeština, 🇬🇧 English, 🇩🇪 Deutsch, Custom):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(4, 2))
        entry_name = ctk.CTkEntry(dialog, placeholder_text="e.g. 🇫🇷 Français")
        entry_name.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(dialog, text="System Prompt Text:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(4, 2))
        txt_prompt = ctk.CTkTextbox(dialog, height=180, wrap="word")
        txt_prompt.pack(fill="both", expand=True, padx=20, pady=2)

        btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_box.pack(pady=12)

        def save():
            name = entry_name.get().strip()
            prompt = txt_prompt.get("1.0", "end").strip()
            if not name:
                messagebox.showwarning("System Prompt", "Prompt name cannot be empty.", parent=dialog)
                return
            if not prompt:
                messagebox.showwarning("System Prompt", "Prompt text cannot be empty.", parent=dialog)
                return
            if any(p.name.lower() == name.lower() for p in self.config.ai.system_prompts):
                messagebox.showwarning("System Prompt", f"A prompt named '{name}' already exists.", parent=dialog)
                return

            new_sp = SystemPromptConfig(name=name, prompt=prompt)
            self.config.ai.system_prompts.append(new_sp)
            self.config_mgr.save()
            self._refresh_sys_prompts_view()
            self.log(f"Added system prompt '{name}'.", "success")
            dialog.destroy()

        ctk.CTkButton(btn_box, text="Save", width=100, fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_HOVER, command=save).pack(side="left", padx=6)
        ctk.CTkButton(btn_box, text="Cancel", width=80, fg_color=theme.CARD_BORDER, command=dialog.destroy).pack(side="left", padx=6)

    def _on_edit_system_prompt(self, index: int):
        if not (0 <= index < len(self.config.ai.system_prompts)):
            return
        sp = self.config.ai.system_prompts[index]

        dialog = ctk.CTkToplevel(self.root)
        dialog.title(f"Edit System Prompt #{index + 1}")
        dialog.geometry("540x440")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_toplevel(dialog, 540, 440)

        ctk.CTkLabel(dialog, text=f"Edit Prompt '{sp.name}'", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(14, 8))

        ctk.CTkLabel(dialog, text="Prompt Name:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(4, 2))
        entry_name = ctk.CTkEntry(dialog)
        entry_name.insert(0, sp.name)
        entry_name.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(dialog, text="System Prompt Text:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(4, 2))
        txt_prompt = ctk.CTkTextbox(dialog, height=180, wrap="word")
        txt_prompt.pack(fill="both", expand=True, padx=20, pady=2)
        txt_prompt.insert("1.0", sp.prompt)

        btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_box.pack(pady=12)

        def save():
            name = entry_name.get().strip()
            prompt = txt_prompt.get("1.0", "end").strip()
            if not name:
                messagebox.showwarning("System Prompt", "Prompt name cannot be empty.", parent=dialog)
                return
            if not prompt:
                messagebox.showwarning("System Prompt", "Prompt text cannot be empty.", parent=dialog)
                return

            self.config.ai.system_prompts[index].name = name
            self.config.ai.system_prompts[index].prompt = prompt
            self.config_mgr.save()
            self._refresh_sys_prompts_view()
            self.log(f"Updated system prompt '{name}'.", "success")
            dialog.destroy()

        ctk.CTkButton(btn_box, text="Save", width=100, fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_HOVER, command=save).pack(side="left", padx=6)
        ctk.CTkButton(btn_box, text="Cancel", width=80, fg_color=theme.CARD_BORDER, command=dialog.destroy).pack(side="left", padx=6)

    def _on_delete_system_prompt(self, index: int):
        if 0 <= index < len(self.config.ai.system_prompts):
            if len(self.config.ai.system_prompts) <= 1:
                messagebox.showwarning("System Prompt", "You must keep at least one system prompt configured.", parent=self.root)
                return
            sp = self.config.ai.system_prompts[index]
            if messagebox.askyesno("Delete prompt", f"Remove system prompt '{sp.name}'?", parent=self.root):
                self.config.ai.system_prompts.pop(index)
                self.config_mgr.save()
                self._refresh_sys_prompts_view()
                self.log(f"Removed system prompt '{sp.name}'.", "info")

    def _on_reset_system_prompts(self):
        if messagebox.askyesno("Reset prompts", "Reset all system prompts to default (🇨🇿 Čeština, 🇬🇧 English, 🇩🇪 Deutsch)?", parent=self.root):
            self.config.ai.system_prompts = get_default_system_prompts()
            self.config_mgr.save()
            self._refresh_sys_prompts_view()
            self.log("Reset system prompts to defaults.", "info")

    def _on_ai_provider_changed(self, choice: str):
        if "gemini" in choice.lower():
            if not self.ai_model_entry.get().strip() or "gpt" in self.ai_model_entry.get().lower():
                self.ai_model_entry.delete(0, "end")
                self.ai_model_entry.insert(0, "gemini-2.5-flash")
        else:
            if not self.ai_model_entry.get().strip() or "gemini" in self.ai_model_entry.get().lower():
                self.ai_model_entry.delete(0, "end")
                self.ai_model_entry.insert(0, "gpt-4o-mini")
        self._update_thinking_budget_slider_limits()

    def _update_thinking_budget_slider_limits(self):
        if not hasattr(self, "ai_thinking_budget_slider") or not hasattr(self, "lbl_thinking_budget"):
            return
        prov_val = self.ai_provider_combo.get() if hasattr(self, "ai_provider_combo") else ""
        model = self.ai_model_entry.get().strip().lower() if hasattr(self, "ai_model_entry") else ""
        is_gemini = "gemini" in prov_val.lower()

        if is_gemini:
            if "pro" in model:
                # Pro models support up to 24576 tokens
                max_val = 24576
                steps = 96
            elif "1.5" in model:
                # Older Gemini 1.5 models do not support thinking
                max_val = 0
                steps = 1
            else:
                # Gemini 2.5 Flash supports up to 8192
                max_val = 8192
                steps = 64
        else:
            # OpenAI / MetaCentrum
            max_val = 8192
            steps = 64

        current_val = self.ai_thinking_budget_slider.get()
        if max_val == 0:
            self.ai_thinking_budget_slider.configure(state="disabled")
            self.lbl_thinking_budget.configure(text="Not supported (non-thinking model)")
        else:
            self.ai_thinking_budget_slider.configure(state="normal", from_=0, to=max_val, number_of_steps=steps)
            if current_val > max_val:
                self.ai_thinking_budget_slider.set(max_val)
            self._on_thinking_budget_changed(self.ai_thinking_budget_slider.get())

    def _format_thinking_budget(self, val: float) -> str:
        tokens = int(round(val / 128.0) * 128)
        if tokens <= 0:
            return "0 (Disabled / Instant ~1s)"
        elif tokens <= 512:
            return f"{tokens} tok. (Fast ~2-3s)"
        elif tokens <= 1024:
            return f"{tokens} tok. (Balanced ~4s)"
        elif tokens <= 4096:
            return f"{tokens} tok. (Deep thinking ~6-10s)"
        elif tokens <= 8192:
            return f"{tokens} tok. (Max for Flash ~15s+)"
        else:
            return f"{tokens} tok. (Pro model / Deep ~20s+)"

    def _on_thinking_budget_changed(self, val: float):
        tokens = int(round(val / 128.0) * 128)
        if hasattr(self, "lbl_thinking_budget"):
            self.lbl_thinking_budget.configure(text=self._format_thinking_budget(tokens))

    def _toggle_ai_key_visibility(self):
        if self.ai_key_entry.cget("show") == "*":
            self.ai_key_entry.configure(show="")
            self.btn_toggle_ai_key.configure(text="🔒")
        else:
            self.ai_key_entry.configure(show="*")
            self.btn_toggle_ai_key.configure(text="👁")

    def _on_test_ai_connection(self):
        prov_val = self.ai_provider_combo.get()
        provider = "gemini" if "gemini" in prov_val.lower() else "openai"
        base_url = self.ai_base_url_entry.get().strip()
        model = self.ai_model_entry.get().strip()
        api_key = self.ai_key_entry.get().strip()
        budget = int(round(self.ai_thinking_budget_slider.get() / 128.0) * 128) if hasattr(self, "ai_thinking_budget_slider") else 1024
        prompt = self.config.ai.system_prompts[0].prompt if self.config.ai.system_prompts else ""

        if not api_key:
            messagebox.showwarning("AI Test", "Please enter an API key first.", parent=self.root)
            return

        test_cfg = AiConfig(
            enabled=True,
            provider=provider,
            base_url=base_url,
            model=model,
            api_key=api_key,
            thinking_budget=budget,
            system_prompts=self.config.ai.system_prompts,
            system_prompt=prompt
        )

        self.btn_test_ai.configure(state="disabled", text="Testing...")
        self.lbl_ai_test_status.configure(text="Pinging AI endpoint...", text_color=theme.TEXT_MUTED)

        def worker():
            ok, msg = AiService.test_connection(test_cfg)
            def done():
                self.btn_test_ai.configure(state="normal", text="Test AI Connection")
                if ok:
                    self.lbl_ai_test_status.configure(text="✅ " + msg[:50], text_color=theme.ACCENT_GREEN)
                    messagebox.showinfo("AI Connection", f"Success!\n\n{msg}", parent=self.root)
                else:
                    self.lbl_ai_test_status.configure(text="❌ " + msg[:40], text_color=theme.ACCENT_RED)
                    messagebox.showerror("AI Connection Failed", f"Connection failed:\n\n{msg}", parent=self.root)
            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Send Concepts & Answer Approval UI Actions
    # --------------------------------------------------------------------------

    def _on_toggle_send_answers(self):
        val = self.var_send_answers.get()
        self.config.send_answers = val
        self.config_mgr.save()
        if self.logger_mode_var.get() == "CONCEPTS":
            if val:
                self.frame_gc_codes_input.pack_forget()
                self.frame_answers_approval.pack(fill="both", expand=True)
                self.logger_right_box.pack(side="right", fill="both", expand=True, padx=5, pady=5)
            else:
                self.frame_answers_approval.pack_forget()
                self.logger_right_box.pack_forget()

    def _on_approval_template_changed(self, choice: str):
        self.config.selected_answer_template = choice
        self.config_mgr.save()
        self._update_approval_preview()

    def _on_ans_blank_clicked(self):
        self.txt_approval_answers.delete("1.0", "end")
        self._update_approval_preview()

    def _on_ans_note_clicked(self):
        note_ans = self._current_approval_data.get("extracted_answers", "")
        self.txt_approval_answers.delete("1.0", "end")
        self.txt_approval_answers.insert("1.0", note_ans)
        self._update_approval_preview()

    def _on_ans_ai_clicked(self):
        self._trigger_ai_for_current_cache()

    def _trigger_ai_for_current_cache(self):
        if not self.config.ai.api_key:
            messagebox.showwarning(
                "AI Key Missing",
                "Please configure your AI API key in the Settings tab first.",
                parent=self.root
            )
            return

        gccode = self._current_approval_data.get("gccode", "")
        name = self._current_approval_data.get("name", "")
        listing = self._current_approval_data.get("listing", "")
        note = self._current_approval_data.get("note", "")

        self.txt_approval_answers.delete("1.0", "end")
        self.txt_approval_answers.insert("1.0", "⏳ Generuji odpovědi pomocí AI (na clusteru může trvat 30–60 s)...")
        self.txt_approval_answers.configure(state="disabled")
        self.btn_send_answer.configure(state="disabled")
        self.btn_skip_answer.configure(state="disabled")

        tmpl_name = self.combo_approval_template.get() if hasattr(self, "combo_approval_template") else ""
        matched_tmpl = next((t for t in self.config.answer_templates if t.name == tmpl_name), None)
        active_ai_prompt_name = matched_tmpl.ai_prompt if (matched_tmpl and matched_tmpl.ai_prompt) else None

        def worker():
            ok, res = AiService.generate_answers(
                config=self.config.ai,
                listing=listing,
                note=note,
                gc_code=gccode,
                cache_name=name,
                prompt_name=active_ai_prompt_name
            )
            def done():
                self.txt_approval_answers.configure(state="normal")
                self.txt_approval_answers.delete("1.0", "end")
                if ok:
                    self.txt_approval_answers.insert("1.0", res)
                    self.log(f"AI generated answers for {gccode}.", "success")
                else:
                    self.txt_approval_answers.insert("1.0", self._current_approval_data.get("extracted_answers", ""))
                    self.log(f"AI generation failed: {res}", "warning")
                    messagebox.showwarning(
                        "AI Generation",
                        f"Failed to generate answers with AI:\n\n{res}\n\nReverting to cache note.",
                        parent=self.root
                    )
                self.btn_send_answer.configure(state="normal")
                self.btn_skip_answer.configure(state="normal")
                self._update_approval_preview()
            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_copy_listing_to_clipboard(self):
        gccode = self._current_approval_data.get("gccode", "")
        name = self._current_approval_data.get("name", "")
        listing = self._current_approval_data.get("listing", "")
        note = self._current_approval_data.get("note", "")

        text_to_copy = (
            f"=== LISTING: {gccode} - {name} ===\n\n"
            f"{listing}\n\n"
            f"=== POZNÁMKA / NOTE ===\n"
            f"{note if note.strip() else '(žádná poznámka / no note)'}"
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(text_to_copy)
        self.log(f"Listing and note for {gccode} copied to clipboard.", "info")

    def _update_approval_preview(self):
        tmpl_name = self.combo_answer_templates.get() if hasattr(self, "combo_answer_templates") else ""
        tmpl_text = ""
        for t in self.config.answer_templates:
            if t.name == tmpl_name:
                tmpl_text = t.text
                break
        if not tmpl_text and self.config.answer_templates:
            tmpl_text = self.config.answer_templates[0].text
        if not tmpl_text:
            tmpl_text = "[...]"

        answers = self.txt_approval_answers.get("1.0", "end").strip() if hasattr(self, "txt_approval_answers") else ""
        gccode = self._current_approval_data.get("gccode", "")
        cname = self._current_approval_data.get("name", "")
        full = format_answer_message(
            template_text=tmpl_text,
            answers=answers,
            username=self.client.logged_in_username if self.client else "",
            gccode=gccode,
            cache_name=cname
        )
        if hasattr(self, "txt_approval_preview"):
            self.txt_approval_preview.configure(state="normal")
            self.txt_approval_preview.delete("1.0", "end")
            self.txt_approval_preview.insert("1.0", full)
            self.txt_approval_preview.configure(state="disabled")

    def _on_approve_send_clicked(self):
        answers = self.txt_approval_answers.get("1.0", "end").strip()
        tmpl_name = self.combo_answer_templates.get()
        self._approval_result = ("SEND", answers, tmpl_name)
        self.btn_send_answer.configure(state="disabled")
        self.btn_skip_answer.configure(state="disabled")
        self.lbl_approval_cache_info.configure(text="⏳ Processing...")
        self._approval_event.set()

    def _on_approve_skip_clicked(self):
        self._approval_result = ("SKIP", "", "")
        self.btn_send_answer.configure(state="disabled")
        self.btn_skip_answer.configure(state="disabled")
        self.lbl_approval_cache_info.configure(text="⏳ Processing...")
        self._approval_event.set()

    def _show_approval_request(self, payload: Dict[str, Any]):
        self._current_approval_data = payload
        gccode = payload.get("gccode", "")
        name = payload.get("name", "")
        ctype = payload.get("cache_type", "")
        owner = payload.get("owner_name", "")
        self.lbl_approval_cache_info.configure(
            text=f"📍 {gccode}: {name} ({ctype})\n👤 Owner: {owner or 'Unknown'}"
        )

        source = (self.config.default_answer_source or "note").lower()
        note_ans = payload.get("extracted_answers", "")

        self.txt_approval_answers.delete("1.0", "end")
        if source == "blank":
            pass
        elif source == "ai" and self.config.ai.api_key:
            self._trigger_ai_for_current_cache()
        else:
            self.txt_approval_answers.insert("1.0", note_ans)

        self._update_approval_preview()
        self.btn_send_answer.configure(state="normal")
        self.btn_skip_answer.configure(state="normal")

    def _reset_approval_panel_to_idle(self):
        if hasattr(self, "lbl_approval_cache_info"):
            self.lbl_approval_cache_info.configure(text="⏳ Waiting for EarthCache / Virtual Cache concepts...")
        if hasattr(self, "txt_approval_answers"):
            self.txt_approval_answers.delete("1.0", "end")
        if hasattr(self, "txt_approval_preview"):
            self.txt_approval_preview.configure(state="normal")
            self.txt_approval_preview.delete("1.0", "end")
            self.txt_approval_preview.configure(state="disabled")
        if hasattr(self, "btn_send_answer"):
            self.btn_send_answer.configure(state="disabled")
        if hasattr(self, "btn_skip_answer"):
            self.btn_skip_answer.configure(state="disabled")
        self._current_approval_data = {}

    def _on_worker_ask_answer(self, payload: Dict[str, Any]) -> Tuple[str, str, str]:
        """
        Invoked from worker thread when an EarthCache or Virtual Cache concept is encountered.
        Blocks worker thread until user takes action in GUI.
        """
        self._approval_event.clear()
        self.root.after(0, lambda: self._show_approval_request(payload))
        self._approval_event.wait()
        return self._approval_result

    def _on_toggle_filters(self):
        self.config.filters.omit_virtual = self.var_omit_virtual.get()
        self.config.filters.omit_webcam = self.var_omit_virtual.get()
        self.config.filters.omit_earthcache = self.var_omit_earth.get()
        self.config.filters.omit_events = self.var_omit_events.get()
        self.config.filters.omit_traditional = self.var_omit_traditional.get()
        self.config.filters.omit_other = self.var_omit_other.get()
        self.config_mgr.save()
        self.log(
            f"Filters updated: Virtual/Webcam={self.config.filters.omit_virtual}, "
            f"Earth={self.config.filters.omit_earthcache}, "
            f"Events={self.config.filters.omit_events}, "
            f"Traditional={self.config.filters.omit_traditional}, "
            f"Other={self.config.filters.omit_other}.",
            "info"
        )

    def _on_toggle_omit_virtual_earth(self):
        self._on_toggle_filters()

    def _on_toggle_geodeep_notes(self):
        self.config.geodeep_notes.include_date = self.var_geodeep_date.get()
        self.config.geodeep_notes.include_drive_in = self.var_geodeep_drive_in.get()
        self.config.geodeep_notes.include_verified = self.var_geodeep_verified.get()
        self.config.geodeep_notes.include_notes = self.var_geodeep_notes.get()
        self.config_mgr.save()
        self.log(
            f"Geodeep notes settings updated: Date={self.config.geodeep_notes.include_date}, "
            f"DriveIn={self.config.geodeep_notes.include_drive_in}, "
            f"Verified={self.config.geodeep_notes.include_verified}, "
            f"Notes={self.config.geodeep_notes.include_notes}.",
            "info"
        )

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

            if hasattr(self, "var_omit_virtual"):
                self.config.filters.omit_virtual = self.var_omit_virtual.get()
                self.config.filters.omit_webcam = self.var_omit_virtual.get()
                self.config.filters.omit_earthcache = self.var_omit_earth.get()
                self.config.filters.omit_events = self.var_omit_events.get()
                self.config.filters.omit_traditional = self.var_omit_traditional.get()
                self.config.filters.omit_other = self.var_omit_other.get()

            if hasattr(self, "var_geodeep_date"):
                self.config.geodeep_notes.include_date = self.var_geodeep_date.get()
                self.config.geodeep_notes.include_drive_in = self.var_geodeep_drive_in.get()
                self.config.geodeep_notes.include_verified = self.var_geodeep_verified.get()
                self.config.geodeep_notes.include_notes = self.var_geodeep_notes.get()

            if hasattr(self, "ai_provider_combo"):
                prov_val = self.ai_provider_combo.get()
                self.config.ai.provider = "gemini" if "gemini" in prov_val.lower() else "openai"
                self.config.ai.base_url = self.ai_base_url_entry.get().strip()
                self.config.ai.model = self.ai_model_entry.get().strip()
                self.config.ai.api_key = self.ai_key_entry.get().strip()
                self.config.ai.enabled = bool(self.config.ai.api_key)
                if hasattr(self, "ai_thinking_budget_slider"):
                    tokens = int(round(self.ai_thinking_budget_slider.get() / 128.0) * 128)
                    self.config.ai.thinking_budget = max(0, tokens)
                if self.config.ai.system_prompts:
                    self.config.ai.system_prompt = self.config.ai.system_prompts[0].prompt
                self.config.default_answer_source = self.ai_default_source_combo.get().strip().lower()

            if self.config_mgr.save():
                messagebox.showinfo("Saved", "Settings successfully saved to config.json!", parent=self.root)
                self.log("Configuration updated and saved.", "success")
            else:
                messagebox.showerror("Error", "Failed to save config.json", parent=self.root)
        except ValueError as e:
            messagebox.showerror("Validation error", f"Invalid number format: {e}", parent=self.root)

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
            if hasattr(self, "btn_coords_pause"):
                self.btn_coords_pause.configure(text="⏸ Pause")
            if hasattr(self, "btn_log_pause"):
                self.btn_log_pause.configure(text="⏸ Pause")
            self.log("Operation resumed.", "info")
        else:
            self.safety.pause()
            if hasattr(self, "btn_coords_pause"):
                self.btn_coords_pause.configure(text="▶ Resume")
            if hasattr(self, "btn_log_pause"):
                self.btn_log_pause.configure(text="▶ Resume")
            self.log("Operation paused by user.", "warning")

    def _stop_operation(self):
        if not self.is_running:
            return
        self.safety.cancel()
        if hasattr(self, "_approval_event"):
            self._approval_result = ("STOP", "", "")
            self._approval_event.set()
        self.log("Stopping operation...", "warning")
        if hasattr(self, "btn_coords_stop"):
            self.btn_coords_stop.configure(state="disabled")
        if hasattr(self, "btn_coords_pause"):
            self.btn_coords_pause.configure(state="disabled")
        if hasattr(self, "btn_log_stop"):
            self.btn_log_stop.configure(state="disabled")
        if hasattr(self, "btn_log_pause"):
            self.btn_log_pause.configure(state="disabled")

    def _update_progress_ui(self, current: int, total: int, percent: float, eta_seconds: Optional[float], text: str):
        self.progress_bar.set(percent / 100.0)
        self.lbl_progress_text.configure(text=f"[{current}/{total}] ({percent:.1f}%) - {text}")
        if eta_seconds is not None:
            m, s = divmod(int(eta_seconds), 60)
            self.lbl_eta.configure(text=f"ETA: {m:02d}m {s:02d}s")

    def _on_operation_done(self, title: str, res: Dict[str, Any]):
        self._flush_log_buffer()
        self._reset_controls()
        if hasattr(self, "_check_active_checkpoint"):
            self._check_active_checkpoint()

        cancelled = res.get("cancelled", False) or getattr(self.safety, "is_cancelled", False)
        success = res.get("success", 0)
        failed = res.get("failed", 0)
        skipped = res.get("skipped", 0)
        elapsed = res.get("elapsed_seconds", 0.0)

        if hasattr(self, "progress_bar"):
            if not cancelled:
                self.progress_bar.set(1.0)
        if hasattr(self, "lbl_progress_text"):
            status_word = "Stopped" if cancelled else "Done"
            summary_short = f"{status_word}: {success} OK, {failed} failed"
            if skipped:
                summary_short += f", {skipped} skipped"
            summary_short += f" ({elapsed:.1f}s)"
            self.lbl_progress_text.configure(text=summary_short)
        if hasattr(self, "lbl_eta"):
            self.lbl_eta.configure(text="ETA: --:--" if cancelled else "ETA: 00:00")

        if not cancelled:
            self._show_completion_dialog(title, res)
        else:
            self.log(f"{title} stopped by user.", "warning")

    def _on_operation_error(self, title: str, error_msg: str):
        self._flush_log_buffer()
        self._reset_controls()
        if hasattr(self, "_check_active_checkpoint"):
            self._check_active_checkpoint()
        if hasattr(self, "lbl_progress_text"):
            self.lbl_progress_text.configure(text=f"Error: {error_msg[:40]}")
        messagebox.showerror(f"{title} Error", f"An unexpected error occurred during {title}:\n\n{error_msg}", parent=self.root)

    def _show_completion_dialog(self, title: str, res: Dict[str, Any]):
        if not CTK_AVAILABLE or not hasattr(self, "root") or not self.root.winfo_exists():
            return

        success = res.get("success", 0)
        failed = res.get("failed", 0)
        skipped = res.get("skipped", 0)
        elapsed = res.get("elapsed_seconds", 0.0)

        try:
            dialog = ctk.CTkToplevel(self.root)
            dialog.title("Operation complete")
            dialog.geometry("380x250")
            dialog.resizable(False, False)
            dialog.transient(self.root)
            dialog.attributes("-topmost", True)

            self._center_toplevel(dialog, 380, 250)

            card = ctk.CTkFrame(dialog, fg_color=theme.CARD_BG, corner_radius=8)
            card.pack(fill="both", expand=True, padx=16, pady=16)

            header_color = theme.ACCENT_GREEN if failed == 0 else theme.ACCENT_RED
            header_text = f"✅ {title} complete!" if failed == 0 else f"⚠️ {title} completed with errors"
            ctk.CTkLabel(
                card,
                text=header_text,
                font=ctk.CTkFont(size=15, weight="bold"),
                text_color=header_color
            ).pack(pady=(12, 10))

            stats_frame = ctk.CTkFrame(card, fg_color="transparent")
            stats_frame.pack(fill="x", padx=16, pady=4)

            ctk.CTkLabel(stats_frame, text=f"• Succeeded: {success}", font=ctk.CTkFont(size=13), text_color=theme.ACCENT_GREEN).pack(anchor="w", pady=2)
            if skipped:
                ctk.CTkLabel(stats_frame, text=f"• Skipped (Already modified): {skipped}", font=ctk.CTkFont(size=13), text_color=theme.ACCENT_BLUE).pack(anchor="w", pady=2)
            fail_color = theme.ACCENT_RED if failed > 0 else theme.TEXT_MUTED
            ctk.CTkLabel(stats_frame, text=f"• Failed: {failed}", font=ctk.CTkFont(size=13), text_color=fail_color).pack(anchor="w", pady=2)
            ctk.CTkLabel(stats_frame, text=f"• Elapsed time: {elapsed:.1f}s", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED).pack(anchor="w", pady=2)

            btn_ok = ctk.CTkButton(
                card,
                text="OK",
                width=100,
                fg_color=theme.ACCENT_GREEN,
                hover_color=theme.ACCENT_GREEN_HOVER,
                command=dialog.destroy
            )
            btn_ok.pack(pady=(12, 10))

            dialog.bind("<Return>", lambda e: dialog.destroy())
            dialog.bind("<Escape>", lambda e: dialog.destroy())
            dialog.bind("<space>", lambda e: dialog.destroy())

            btn_ok.focus_set()
        except Exception:
            pass

    def _reset_controls(self):
        self.is_running = False
        self._flush_log_buffer()
        if hasattr(self, "btn_coords_start"):
            self.btn_coords_start.configure(state="normal")
        if hasattr(self, "btn_coords_pause"):
            self.btn_coords_pause.configure(state="disabled", text="⏸ Pause")
        if hasattr(self, "btn_coords_stop"):
            self.btn_coords_stop.configure(state="disabled")
        if hasattr(self, "btn_log_start"):
            self.btn_log_start.configure(state="normal")
        if hasattr(self, "btn_log_pause"):
            self.btn_log_pause.configure(state="disabled", text="⏸ Pause")
        if hasattr(self, "btn_log_stop"):
            self.btn_log_stop.configure(state="disabled")
        if hasattr(self, "lbl_eta"):
            self.lbl_eta.configure(text="ETA: --:--")
        self._reset_approval_panel_to_idle()

    def run(self):
        """Start the GUI main loop."""
        self.root.mainloop()


if __name__ == "__main__":
    app = UltimateApp()
    app.run()

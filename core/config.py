"""
Configuration manager for GC Autologger.
Handles loading, updating, migrating, and saving settings to config.json.
Provides full backwards compatibility with legacy InputData.json.
"""

import os
import sys
import json
import shutil
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any


def get_base_dir() -> str:
    """
    Get the persistent directory for storing user data (config.json, session_cookies.json, runs/).
    When packaged as a PyInstaller executable, returns the folder containing the .exe binary.
    When running in development, returns the repository root directory.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_bundle_dir() -> str:
    """
    Get the directory containing bundled read-only assets (assets/, config.example.json).
    When packaged with PyInstaller --onefile, resources are unpacked into sys._MEIPASS.
    When running in development, returns the repository root directory.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class AccountConfig:
    id: str
    username: str
    password: str = ""
    is_default: bool = False


@dataclass
class SafetyConfig:
    min_delay_seconds: float = 0.7
    max_delay_seconds: float = 1.2
    breather_interval: int = 50
    breather_duration_seconds: float = 4.0
    auto_relogin: bool = True


@dataclass
class FilterConfig:
    omit_virtual: bool = False
    omit_earthcache: bool = False
    omit_events: bool = False
    omit_webcam: bool = False
    omit_traditional: bool = False
    omit_other: bool = False

    @property
    def has_any_enabled(self) -> bool:
        return (
            self.omit_virtual
            or self.omit_earthcache
            or self.omit_events
            or self.omit_webcam
            or self.omit_traditional
            or self.omit_other
        )

    @property
    def omit_earth(self) -> bool:
        """Alias for omit_earthcache."""
        return self.omit_earthcache

    @omit_earth.setter
    def omit_earth(self, val: bool) -> None:
        self.omit_earthcache = val

    @property
    def omit_virtual_earth_webcam(self) -> bool:
        """Backwards compatibility property."""
        return self.omit_virtual and self.omit_earthcache and self.omit_webcam

    @omit_virtual_earth_webcam.setter
    def omit_virtual_earth_webcam(self, val: bool) -> None:
        self.omit_virtual = val
        self.omit_earthcache = val
        self.omit_webcam = val


@dataclass
class UpdatesConfig:
    auto_check: bool = True  # Check for updates automatically on startup


@dataclass
class GuiConfig:
    theme: str = "dark"
    color_theme: str = "blue"


@dataclass
class GeodeepNotesConfig:
    include_date: bool = False
    include_drive_in: bool = False
    include_verified: bool = False
    include_notes: bool = False

    @property
    def has_any_enabled(self) -> bool:
        return self.include_date or self.include_drive_in or self.include_verified or self.include_notes


@dataclass
class SystemPromptConfig:
    name: str = ""
    prompt: str = ""


def get_default_system_prompts() -> List[SystemPromptConfig]:
    return [
        SystemPromptConfig(
            name="🇨🇿 Čeština",
            prompt=(
                "Jsi zkušený asistent pro EarthCache a Virtual geocaching v České republice.\n"
                "Tvým úkolem je na základě listingu (a případné poznámky hráče) připravit velmi stručné, věcné odpovědi na otázky pro ownera keše.\n"
                "Pravidla:\n"
                "1. Odpověz v češtině, formátuj striktně jako očíslovaný seznam: 1) ..., 2) ...\n"
                "2. Pokud jsou odpovědi nebo nápovědy uvedeny v poznámce hráče, použij je přednostně a zformuluj je přesně.\n"
                "3. Pokud listing ani poznámka přímo neobsahuje odpověď na terénní či historickou otázku (např. nadmořská výška, počátek těžby), použij své všeobecné geografické a historické znalosti dané lokality a doplň nejreálnější hodnotu.\n"
                "4. Pokud je některý bod v otázkách pokyn k přiložení fotografie do logu (nikoliv otázka s odpovědí), napiš: 'Foto přiloženo k logu.'.\n"
                "5. Neuváděj žádné úvodní ani závěrečné zdvořilostní fráze (žádné 'Ahoj', žádné 'S pozdravem'), pouze samotné číslované odpovědi."
            )
        ),
        SystemPromptConfig(
            name="🇬🇧 English",
            prompt=(
                "You are an expert assistant for EarthCache and Virtual geocaching.\n"
                "Your task is to prepare very concise, factual answers to the listing questions for the cache owner based on the cache listing (and the player's personal field note, if any).\n"
                "Rules:\n"
                "1. Respond in English, format strictly as a numbered list: 1) ..., 2) ...\n"
                "2. If answers or hints are provided in the player's note, prioritize them and formulate them accurately.\n"
                "3. If neither the listing nor note explicitly contains the answer to a field or historical question (e.g. altitude, mining origins), use your general geographic and historical knowledge of the site to supply the most realistic value.\n"
                "4. If an item in the questions is an instruction to attach a photo to the log (not a question to be answered), write: 'Photo attached to the log.'.\n"
                "5. Do not include any introductory or concluding pleasantries (no 'Hello', no 'Best regards'), output only the numbered answers."
            )
        ),
        SystemPromptConfig(
            name="🇩🇪 Deutsch",
            prompt=(
                "Du bist ein erfahrener Assistent für EarthCache- und Virtual-Geocaching.\n"
                "Deine Aufgabe ist es, anhand des Cache-Listings (und eventueller persönlicher Feldnotizen des Spielers) sehr präzise, sachliche Antworten auf die Listing-Fragen für den Cache-Owner zu verfassen.\n"
                "Regeln:\n"
                "1. Antworte auf Deutsch, formatiere streng als nummerierte Liste: 1) ..., 2) ...\n"
                "2. Falls Antworten oder Hinweise in den Notizen des Spielers vorhanden sind, verwende diese vorrangig und formuliere sie exakt.\n"
                "3. Wenn weder Listing noch Notizen die Antwort auf eine Gelände- oder historische Frage enthalten (z. B. Höhe über dem Meeresspiegel, Beginn des Abbaus), nutze dein allgemeines geografisches und historisches Wissen über den Ort, um den realistischsten Wert einzusetzen.\n"
                "4. Wenn ein Punkt in den Fragen eine Anweisung zum Hochladen eines Fotos ins Log ist (keine zu beantwortende Frage), schreibe: 'Foto im Log beigefügt.'.\n"
                "5. Keine einleitenden oder abschließenden Grußformeln (kein 'Hallo', kein 'Viele Grüße'), gib ausschließlich die nummerierten Antworten aus."
            )
        )
    ]


@dataclass
class AnswerTemplate:
    name: str = ""
    text: str = ""
    ai_prompt: str = ""


@dataclass
class AiConfig:
    enabled: bool = False
    provider: str = "openai"  # "openai" (OpenAI / MetaCentrum / Custom) or "gemini"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    base_url: str = ""
    thinking_budget: int = 1024
    system_prompts: List[SystemPromptConfig] = field(default_factory=get_default_system_prompts)
    system_prompt: str = ""

    def get_system_prompt(self, name: Optional[str] = None) -> str:
        if name:
            for sp in self.system_prompts:
                if sp.name.strip().lower() == name.strip().lower():
                    return sp.prompt
        if self.system_prompts:
            return self.system_prompts[0].prompt
        return self.system_prompt or (get_default_system_prompts()[0].prompt)


@dataclass
class AppConfig:
    accounts: List[AccountConfig] = field(default_factory=list)
    folder_path: str = ""
    last_browse_dir: str = ""
    log_templates: List[str] = field(default_factory=lambda: [
        "TFTC.",
        "DFDC.",
        "Díky za keš."
    ])
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)
    geodeep_notes: GeodeepNotesConfig = field(default_factory=GeodeepNotesConfig)
    answer_templates: List[AnswerTemplate] = field(default_factory=lambda: [
        AnswerTemplate(
            name="Default",
            text="Ahoj, zasílám odpovědi na otázky z listingu:\n[...]\n\nDíky za keš!\n{username}"
        )
    ])
    selected_answer_template: str = "Default"
    send_answers: bool = False
    default_answer_source: str = "note"  # "blank", "note", "ai"
    ai: AiConfig = field(default_factory=AiConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)

    @property
    def auto_update(self) -> bool:
        return self.updates.auto_check

    @auto_update.setter
    def auto_update(self, val: bool) -> None:
        self.updates.auto_check = bool(val)


class ConfigManager:
    """Manages reading and writing application configuration."""

    def __init__(self, config_dir: Optional[str] = None):
        if not config_dir:
            config_dir = get_base_dir()
        self.config_dir = config_dir
        self.config_file = os.path.join(self.config_dir, "config.json")
        bundle_example = os.path.join(get_bundle_dir(), "config.example.json")
        self.example_file = bundle_example if os.path.exists(bundle_example) else os.path.join(self.config_dir, "config.example.json")
        self.config: AppConfig = AppConfig()
        self.load()

    def load(self) -> AppConfig:
        """Load configuration from config.json or migrate from InputData.json."""
        if not os.path.exists(self.config_file):
            # Check for legacy InputData.json in parent directory
            legacy_path = os.path.join(os.path.dirname(self.config_dir), "gc_autologger", "InputData.json")
            if os.path.exists(legacy_path):
                self._migrate_legacy_config(legacy_path)
            elif os.path.exists(self.example_file):
                try:
                    shutil.copy(self.example_file, self.config_file)
                except Exception:
                    pass

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.config = self._dict_to_config(data)
            except Exception as e:
                print(f"Warning: Failed to load config.json ({e}). Using defaults.")
                self.config = AppConfig()
        else:
            self.config = AppConfig()

        return self.config

    def save(self) -> bool:
        """Save current configuration to config.json with pretty indentation."""
        try:
            raw_dict = {
                "accounts": [asdict(a) for a in self.config.accounts],
                "folder_path": self.config.folder_path,
                "last_browse_dir": self.config.last_browse_dir,
                "log_templates": self.config.log_templates,
                "safety": asdict(self.config.safety),
                "filters": asdict(self.config.filters),
                "gui": asdict(self.config.gui),
                "geodeep_notes": asdict(self.config.geodeep_notes),
                "answer_templates": [asdict(t) for t in self.config.answer_templates],
                "selected_answer_template": self.config.selected_answer_template,
                "send_answers": self.config.send_answers,
                "default_answer_source": self.config.default_answer_source,
                "ai": asdict(self.config.ai),
                "updates": asdict(self.config.updates)
            }
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(raw_dict, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving config.json: {e}")
            return False

    def get_default_account(self) -> Optional[AccountConfig]:
        """Return the default account, or the first account with a password, or first account, or None."""
        if not self.config.accounts:
            return None
        for acc in self.config.accounts:
            if acc.is_default:
                return acc
        for acc in self.config.accounts:
            if acc.password:
                return acc
        return self.config.accounts[0]

    def get_account_by_id(self, acc_id: str) -> Optional[AccountConfig]:
        for acc in self.config.accounts:
            if acc.id == acc_id:
                return acc
        return None

    def get_account_by_username(self, username: str) -> Optional[AccountConfig]:
        for acc in self.config.accounts:
            if acc.username.lower() == username.lower():
                return acc
        return None

    def renumber_accounts(self):
        """Renumber all accounts sequentially from 1 to N and save."""
        for idx, acc in enumerate(self.config.accounts, 1):
            acc.id = str(idx)
        self.save()

    def add_or_update_account(self, username: str, password: str = "", is_default: bool = False, acc_id: Optional[str] = None) -> AccountConfig:
        if not acc_id:
            # Generate next numeric ID
            acc_id = str(len(self.config.accounts) + 1)

        if is_default:
            for a in self.config.accounts:
                a.is_default = False

        # Check if updating existing
        for idx, a in enumerate(self.config.accounts):
            if a.id == acc_id or a.username.lower() == username.lower():
                self.config.accounts[idx] = AccountConfig(id=a.id, username=username, password=password, is_default=is_default)
                self.renumber_accounts()
                return self.config.accounts[idx]

        new_acc = AccountConfig(id=acc_id, username=username, password=password, is_default=is_default)
        self.config.accounts.append(new_acc)
        self.renumber_accounts()
        return new_acc

    def remove_account(self, acc_id: str) -> bool:
        initial_len = len(self.config.accounts)
        self.config.accounts = [a for a in self.config.accounts if a.id != acc_id]
        if len(self.config.accounts) < initial_len:
            if self.config.accounts and not any(a.is_default for a in self.config.accounts):
                self.config.accounts[0].is_default = True
            self.renumber_accounts()
            return True
        return False

    def _dict_to_config(self, data: Dict[str, Any]) -> AppConfig:
        # Accounts
        accounts = []
        raw_accounts = data.get("accounts", [])
        for item in raw_accounts:
            accounts.append(AccountConfig(
                id=str(item.get("id", "")),
                username=item.get("username", ""),
                password=item.get("password", ""),
                is_default=bool(item.get("is_default", False))
            ))

        # Safety
        raw_safety = data.get("safety", {})
        safety = SafetyConfig(
            min_delay_seconds=float(raw_safety.get("min_delay_seconds", 0.7)),
            max_delay_seconds=float(raw_safety.get("max_delay_seconds", 1.2)),
            breather_interval=int(raw_safety.get("breather_interval", 50)),
            breather_duration_seconds=float(raw_safety.get("breather_duration_seconds", 4.0)),
            auto_relogin=bool(raw_safety.get("auto_relogin", True))
        )

        # Filters
        raw_filters = data.get("filters", {})
        legacy_val = raw_filters.get("omit_virtual_earth_webcam", raw_filters.get("omit_virtual_and_earth", False))
        omit_v = bool(raw_filters.get("omit_virtual", legacy_val))
        omit_ec = bool(raw_filters.get("omit_earthcache", raw_filters.get("omit_earth", legacy_val)))
        omit_ev = bool(raw_filters.get("omit_events", False))
        omit_wc = bool(raw_filters.get("omit_webcam", legacy_val))

        omit_trad = bool(raw_filters.get("omit_traditional", False))
        omit_oth = bool(raw_filters.get("omit_other", False))

        filters = FilterConfig(
            omit_virtual=omit_v,
            omit_earthcache=omit_ec,
            omit_events=omit_ev,
            omit_webcam=omit_wc,
            omit_traditional=omit_trad,
            omit_other=omit_oth
        )

        # GUI
        raw_gui = data.get("gui", {})
        gui = GuiConfig(
            theme=str(raw_gui.get("theme", "dark")),
            color_theme=str(raw_gui.get("color_theme", "blue"))
        )

        # Geodeep Notes
        raw_geodeep = data.get("geodeep_notes", {})
        geodeep_notes = GeodeepNotesConfig(
            include_date=bool(raw_geodeep.get("include_date", False)),
            include_drive_in=bool(raw_geodeep.get("include_drive_in", False)),
            include_verified=bool(raw_geodeep.get("include_verified", False)),
            include_notes=bool(raw_geodeep.get("include_notes", False))
        )

        # Answer Templates
        raw_ans = data.get("answer_templates", [])
        ans_templates = []
        if isinstance(raw_ans, list):
            for it in raw_ans:
                if isinstance(it, dict) and "name" in it and "text" in it:
                    ans_templates.append(AnswerTemplate(
                        name=str(it["name"]),
                        text=str(it["text"]),
                        ai_prompt=str(it.get("ai_prompt", ""))
                    ))
                elif isinstance(it, str):
                    ans_templates.append(AnswerTemplate(name="Default", text=it))
        if not ans_templates:
            ans_templates = AppConfig().answer_templates

        # AI Assistant Config
        raw_ai = data.get("ai", {})
        raw_prompts = raw_ai.get("system_prompts", [])
        prompts = []
        if isinstance(raw_prompts, list) and raw_prompts:
            for it in raw_prompts:
                if isinstance(it, dict) and "name" in it and "prompt" in it:
                    prompts.append(SystemPromptConfig(name=str(it["name"]), prompt=str(it["prompt"])))
        if not prompts:
            legacy_sp = str(raw_ai.get("system_prompt", "")).strip()
            prompts = get_default_system_prompts()
            if legacy_sp:
                prompts[0].prompt = legacy_sp

        ai_cfg = AiConfig(
            enabled=bool(raw_ai.get("enabled", False)),
            provider=str(raw_ai.get("provider", "openai")),
            api_key=str(raw_ai.get("api_key", "")),
            model=str(raw_ai.get("model", "gpt-4o-mini")),
            base_url=str(raw_ai.get("base_url", "")),
            thinking_budget=int(raw_ai.get("thinking_budget", 1024)),
            system_prompts=prompts,
            system_prompt=prompts[0].prompt if prompts else ""
        )

        # Updates
        raw_updates = data.get("updates", {})
        auto_chk = bool(raw_updates.get("auto_check", data.get("auto_update", True)))
        updates_cfg = UpdatesConfig(auto_check=auto_chk)

        return AppConfig(
            accounts=accounts,
            folder_path=str(data.get("folder_path", "")),
            last_browse_dir=str(data.get("last_browse_dir", data.get("folder_path", ""))),
            log_templates=list(data.get("log_templates", [])),
            safety=safety,
            filters=filters,
            gui=gui,
            geodeep_notes=geodeep_notes,
            answer_templates=ans_templates,
            selected_answer_template=str(data.get("selected_answer_template", "Default")),
            send_answers=bool(data.get("send_answers", False)),
            default_answer_source=str(data.get("default_answer_source", "note")),
            ai=ai_cfg,
            updates=updates_cfg
        )

    def _migrate_legacy_config(self, legacy_path: str) -> None:
        """Migrate legacy InputData.json format to new AppConfig format."""
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            accounts = []
            for key, value in data.items():
                if key.startswith("Username_"):
                    idx = key.split("_")[1]
                    pwd_key = f"Password_{idx}"
                    if pwd_key in data:
                        accounts.append(AccountConfig(
                            id=idx,
                            username=value,
                            password=data[pwd_key],
                            is_default=(idx == "1")
                        ))

            if not accounts and "Username" in data and "Password" in data:
                accounts.append(AccountConfig(id="1", username=data["Username"], password=data["Password"], is_default=True))

            templates = []
            for key, value in data.items():
                if key.startswith("LogTemplate_"):
                    templates.append(value)

            self.config = AppConfig(
                accounts=accounts,
                folder_path=data.get("FolderPath", ""),
                log_templates=templates or AppConfig().log_templates
            )
            self.save()
            print(f"Migrated legacy configuration from {legacy_path} -> {self.config_file}")
        except Exception as e:
            print(f"Failed to migrate legacy config: {e}")

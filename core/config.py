"""
Configuration manager for GC Autologger.
Handles loading, updating, migrating, and saving settings to config.json.
Provides full backwards compatibility with legacy InputData.json.
"""

import os
import json
import shutil
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any


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
    omit_virtual_earth_webcam: bool = False


@dataclass
class GuiConfig:
    theme: str = "dark"
    color_theme: str = "blue"


@dataclass
class AppConfig:
    accounts: List[AccountConfig] = field(default_factory=list)
    folder_path: str = ""
    log_templates: List[str] = field(default_factory=lambda: [
        "TFTC.",
        "DFDC.",
        "Díky za keš."
    ])
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)


class ConfigManager:
    """Manages reading and writing application configuration."""

    def __init__(self, config_dir: Optional[str] = None):
        if not config_dir:
            config_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config_dir = config_dir
        self.config_file = os.path.join(self.config_dir, "config.json")
        self.example_file = os.path.join(self.config_dir, "config.example.json")
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
                "log_templates": self.config.log_templates,
                "safety": asdict(self.config.safety),
                "filters": asdict(self.config.filters),
                "gui": asdict(self.config.gui)
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

    def add_or_update_account(self, username: str, password: str = "", is_default: bool = False, acc_id: Optional[str] = None) -> AccountConfig:
        if not acc_id:
            # Generate next numeric ID
            existing_ids = [int(a.id) for a in self.config.accounts if a.id.isdigit()]
            acc_id = str(max(existing_ids, default=0) + 1)

        if is_default:
            for a in self.config.accounts:
                a.is_default = False

        # Check if updating existing
        for idx, a in enumerate(self.config.accounts):
            if a.id == acc_id:
                self.config.accounts[idx] = AccountConfig(id=acc_id, username=username, password=password, is_default=is_default)
                self.save()
                return self.config.accounts[idx]

        new_acc = AccountConfig(id=acc_id, username=username, password=password, is_default=is_default)
        self.config.accounts.append(new_acc)
        self.save()
        return new_acc

    def remove_account(self, acc_id: str) -> bool:
        initial_len = len(self.config.accounts)
        self.config.accounts = [a for a in self.config.accounts if a.id != acc_id]
        if len(self.config.accounts) < initial_len:
            if self.config.accounts and not any(a.is_default for a in self.config.accounts):
                self.config.accounts[0].is_default = True
            self.save()
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
        val = raw_filters.get("omit_virtual_earth_webcam", raw_filters.get("omit_virtual_and_earth", False))
        filters = FilterConfig(
            omit_virtual_earth_webcam=bool(val)
        )

        # GUI
        raw_gui = data.get("gui", {})
        gui = GuiConfig(
            theme=str(raw_gui.get("theme", "dark")),
            color_theme=str(raw_gui.get("color_theme", "blue"))
        )

        return AppConfig(
            accounts=accounts,
            folder_path=str(data.get("folder_path", "")),
            log_templates=list(data.get("log_templates", [])),
            safety=safety,
            filters=filters,
            gui=gui
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

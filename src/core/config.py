"""
Configuration manager for TwinRails.
Stores settings in ~/.ai_session_limits/config.json and manages Windows startup.
Automatically migrates existing settings from ~/.claude_taskbar_widget if present.
The settings folder keeps its old name so upgrades retain saved credentials.
"""

import json
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List

from .dpapi import encrypt, decrypt
protect_data = encrypt
unprotect_data = decrypt

CONFIG_DIR = Path.home() / ".ai_session_limits"
CONFIG_FILE = CONFIG_DIR / "config.json"
LEGACY_CONFIG_FILE = Path.home() / ".claude_taskbar_widget" / "config.json"


@dataclass
class WidgetConfig:
    enabled_providers: List[str] = field(default_factory=lambda: ["claude", "antigravity"])
    claude_auth_mode: str = "auto"  # "auto" | "oauth" | "cookie"
    claude_session_key_enc: str = ""
    claude_org_id: str = ""
    poll_interval_seconds: int = 300
    # New installs already use the five-minute default. Old config files lack
    # this marker and are upgraded once in load(); later user choices are kept.
    poll_interval_relaxed: bool = True
    show_docked_bar: bool = True
    docked_bar_x: int = -1
    docked_bar_y: int = -1
    docked_bar_opacity: float = 0.92
    docked_bar_locked: bool = False
    always_on_top: bool = True
    start_with_windows: bool = False
    tray_hint_shown: bool = False
    demo_mode: bool = False
    # Up to 4 metric keys to show on the taskbar bar:
    # e.g. ["claude_five_hour", "gemini_5h"]
    widget_metric_keys: list = field(default_factory=lambda: ["claude_five_hour", "gemini_5h"])
    # Metric keys shown in compact mode (text/stats only, no progress bar) on the taskbar
    compact_metric_keys: list = field(default_factory=list)
    # Metric keys to show only in flyout (not on taskbar bar)
    flyout_only_metric_keys: list = field(default_factory=list)
    pacing_color_mode: str = "dynamic"  # "dynamic" (Option B) or "fixed" (Option A)
    pacing_fixed_delta: float = 10.0
    percentage_display_mode: str = "used"  # "used" or "remaining"
    show_time_divisions: bool = True  # 5-hour and 7-day guides, in both bar views
    # Manual light/dark background for the taskbar bar -- replaced an
    # earlier attempt at auto-detecting the real taskbar color (sampling a
    # pixel via GetPixel). That turned out to be unreliable on top of
    # unrelated Tk/win32 embedding flakiness on some systems, so the user
    # picks colors that match their own taskbar instead. See
    # theme.adaptive_bar_theme(), which derives matching text/track colors
    # from whichever of these two is active.
    # "dark" or "light". No longer set anywhere: the bar follows Windows'
    # taskbar theme. Only used if that can't be read from the registry.
    bar_theme_mode: str = "dark"
    bar_bg_color_dark: str = "#1C1B1A"
    bar_bg_color_light: str = "#F3F3F3"
    # When True the docked bar paints no background at all -- the real
    # taskbar shows through and only the bars, text and pulse mark are
    # drawn. This matches ANY taskbar, including a translucent Windows 11
    # one whose color changes across its own width with the wallpaper
    # behind it, which no single painted color can do.
    #
    # The color above is still used, for two things: it is the colorkey
    # (so it must not collide with a drawn color), and it stands in for
    # "what is behind the bar" when deriving text colors that have to stay
    # legible against it -- see theme.transparent_bar_theme().
    bar_transparent_bg: bool = True

    # Backward compatibility properties for Claude
    @property
    def session_key(self) -> str:
        if not self.claude_session_key_enc:
            return ""
        try:
            return unprotect_data(self.claude_session_key_enc)
        except Exception as e:
            # DPAPI ties ciphertext to the Windows user account that created
            # it (see dpapi.py) -- CryptUnprotectData raises if config.json
            # was copied from another machine or account, or the account's
            # DPAPI master key was reset. That used to propagate straight out
            # of this property, and both ProviderManager and SettingsWindow
            # read it during __init__, so the whole app failed to start with
            # no window shown at all (silent under pythonw, which is how it
            # is actually launched via run_silent.vbs). Treat an
            # undecryptable blob the same as no key set: providers already
            # handle that by prompting the user to re-enter it in Settings,
            # which is exactly the right recovery action here too.
            print(f"[Config] Could not decrypt stored Claude session key: {e}")
            return ""

    @session_key.setter
    def session_key(self, value: str):
        if not value:
            self.claude_session_key_enc = ""
        else:
            self.claude_session_key_enc = protect_data(value)

    @property
    def org_id(self) -> str:
        return self.claude_org_id

    @org_id.setter
    def org_id(self, value: str):
        self.claude_org_id = value


class ConfigManager:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.config = self.load()
        self._migrate_startup_shortcut_name()

    def _migrate_startup_shortcut_name(self):
        """Rename our existing Windows Startup link without changing its target."""
        if os.name != "nt" or not self.config.start_with_windows:
            return
        startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        current = startup_dir / "TwinRails.lnk"
        old_links = [startup_dir / name for name in ("AISessionLimitsWidget.lnk", "ClaudeTaskbarWidget.lnk")]
        try:
            if not current.exists():
                for old in old_links:
                    if old.exists():
                        old.rename(current)
                        break
            if current.exists():
                for old in old_links:
                    if old.exists():
                        old.unlink()
        except OSError as e:
            print(f"Could not rename TwinRails startup shortcut: {e}")

    def load(self) -> WidgetConfig:
        target_file = CONFIG_FILE
        if not target_file.exists() and LEGACY_CONFIG_FILE.exists():
            target_file = LEGACY_CONFIG_FILE

        if not target_file.exists():
            cfg = WidgetConfig()
            self.save(cfg)
            return cfg

        try:
            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Map legacy session_key_enc to claude_session_key_enc if needed
            if "session_key_enc" in data and "claude_session_key_enc" not in data:
                data["claude_session_key_enc"] = data["session_key_enc"]
            if "org_id" in data and "claude_org_id" not in data:
                data["claude_org_id"] = data["org_id"]

            cfg = WidgetConfig(**{k: v for k, v in data.items() if k in WidgetConfig.__annotations__})
            # Ensure both claude and antigravity are enabled by default
            if not cfg.enabled_providers:
                cfg.enabled_providers = ["claude", "antigravity"]

            # Older saves spell out the 60s default, so a changed dataclass
            # default alone cannot reach them. Treat the removed 30s option
            # and the old 60s default as legacy values. A saved 120s choice
            # was deliberate and must survive. We cannot tell an explicitly
            # chosen 60s from the old default; it changes once, then the user
            # can select 60s again without another migration.
            if data.get("poll_interval_relaxed") is not True:
                cfg.poll_interval_relaxed = True
                if cfg.poll_interval_seconds in (30, 60):
                    cfg.poll_interval_seconds = 300
                self.save(cfg)
            return cfg
        except Exception:
            return WidgetConfig()

    def save(self, cfg: Optional[WidgetConfig] = None):
        if cfg is not None:
            self.config = cfg
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            payload = asdict(self.config)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")

    def toggle_startup(self, enable: bool) -> bool:
        """Create or remove Windows startup shortcut for unified widget."""
        try:
            startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            if not startup_dir.exists():
                return False

            shortcut_path = startup_dir / "TwinRails.lnk"
            old_shortcuts = [startup_dir / name for name in ("AISessionLimitsWidget.lnk", "ClaudeTaskbarWidget.lnk")]

            vbs_launcher = Path(__file__).resolve().parent.parent.parent / "run_silent.vbs"

            if enable:
                target = str(vbs_launcher.resolve())
                work_dir = str(vbs_launcher.parent.resolve())
                ps_script = f"""
                $ws = New-Object -ComObject WScript.Shell;
                $s = $ws.CreateShortcut('{str(shortcut_path)}');
                $s.TargetPath = 'wscript.exe';
                $s.Arguments = '\"\"\"{target}\"\"\"';
                $s.WorkingDirectory = '{work_dir}';
                $s.Save();
                """
                os.system(f'powershell -WindowStyle Hidden -Command "{ps_script.strip()}"')
                if shortcut_path.exists():
                    for old in old_shortcuts:
                        if old.exists():
                            old.unlink()
                self.config.start_with_windows = True
            else:
                if shortcut_path.exists():
                    shortcut_path.unlink()
                for old in old_shortcuts:
                    if old.exists():
                        old.unlink()
                self.config.start_with_windows = False

            self.save()
            return True
        except Exception as e:
            print(f"Failed to toggle startup: {e}")
            return False

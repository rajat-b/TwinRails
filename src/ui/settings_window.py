"""
Settings dialog for TwinRails.
Manage active AI providers (Claude, Antigravity/Gemini), credentials,
metrics selection, pacing colors, and auto-start options.
"""

import tkinter as tk
import threading
import re
from typing import Callable, Optional, Union, List

from .theme import (
    BG_MAIN, BG_CARD, BG_HOVER, BORDER_CARD,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    CLAUDE_CORAL, GEMINI_BLUE, WARNING_AMBER, SUCCESS_GREEN, FONT_FAMILY,
    FONT_SIZE_CAPTION, FONT_SIZE_BODY, FONT_SIZE_HEADING
)
from .dpi import get_work_area_for_point_or_window, get_dpi_scale_for_window
from .scrollbar import DarkScrollbar
from .title_bar import use_dark_title_bar
from .text_wrap import wrap_to_width
from ..core.config import ConfigManager, WidgetConfig
from ..core.claude_client import ClaudeClient
from ..core.claude_oauth import (
    ClaudeTokenExpired, ClaudeTokenReadError, ClaudeTokenUnavailable, read_claude_code_token,
)
from ..core.models import UnifiedUsageState, ClaudeUsageState

MAX_WIDGET_METRICS = 4

_DEFAULT_CHOICES = [
    ("claude_five_hour", "✱ Claude · Session Limit (5h)"),
    ("gemini_5h", "✦ Gemini · 5-Hour Limit (5h)"),
    ("claude_seven_day", "✱ Claude · Weekly · All Models (7d)"),
    ("gemini_weekly", "✦ Gemini · Weekly Limit (7d)"),
    ("3p_5h", "✦ Claude & GPT · 5-Hour (Antigravity)"),
    ("usage_credits", "✱ Claude · Usage Credits ($)")
]


def _metric_choices(current_state, saved_keys) -> list[tuple[str, str]]:
    """Offer every reported Claude model limit and keep saved ones through an offline refresh."""
    choices = list(_DEFAULT_CHOICES)
    seen = {key for key, _ in choices}
    if current_state is not None:
        for metric in current_state.all_metrics:
            if metric.provider_id != "claude" or metric.key in seen:
                continue
            choices.append((metric.key, f"✱ Claude · {metric.label}"))
            seen.add(metric.key)

    for key in saved_keys:
        if key in seen:
            continue
        if key.startswith("limits_weekly_scoped_"):
            name = key[len("limits_weekly_scoped_"):].rsplit("_", 1)[0]
            label = f"✱ Claude · Weekly · {name.replace('_', ' ').title()} (unavailable now)"
        elif key == "limits_weekly_scoped":
            label = "✱ Claude · Weekly model limit (unavailable now)"
        elif key == "cloud_session_credits":
            label = "✱ Claude · Cloud Session Credits (unavailable now)"
        elif key.startswith("included_credit_"):
            label = "✱ Claude · Included Credit (unavailable now)"
        else:
            label = f"Saved metric · {key} (unavailable now)"
        choices.append((key, label))
        seen.add(key)
    return choices


def clean_cookie_val(raw: str, key_name: str = "") -> str:
    s = raw.strip().strip("\"' \t\r\n")
    if key_name and f"{key_name}=" in s:
        m = re.search(rf"{key_name}=([^;]+)", s)
        if m:
            s = m.group(1).strip()
    elif "sessionKey=" in s and key_name == "sessionKey":
        m = re.search(r"sessionKey=([^;]+)", s)
        if m:
            s = m.group(1).strip()
    return s.strip("\"' \t\r\n")


class SettingsWindow:
    def __init__(
        self,
        master: tk.Tk,
        config_manager: ConfigManager,
        on_save_callback: Callable[[WidgetConfig], None],
        current_state: Optional[Union[UnifiedUsageState, ClaudeUsageState]] = None
    ):
        self.master = master
        self.cfg_mgr = config_manager
        self.on_save = on_save_callback
        self.current_state = current_state

        self.window = tk.Toplevel(master)
        self.window.title("TwinRails Settings")
        self.window.configure(bg=BG_MAIN)
        use_dark_title_bar(self.window)
        self.window.resizable(True, True)
        self.window.wm_attributes("-topmost", True)

        try:
            scale = get_dpi_scale_for_window(self.window.winfo_id())
        except Exception:
            scale = 1.0

        self.width = round(500 * scale)
        self.height = round(720 * scale)
        self._center_window()
        self.window.minsize(round(480 * scale), round(620 * scale))

        self._show_key = False
        self._build_ui()

    def _center_window(self):
        try:
            hwnd = self.window.winfo_id()
        except Exception:
            hwnd = None
        wl, wt, wr, wb = get_work_area_for_point_or_window(hwnd)
        x = wl + max(0, ((wr - wl) - self.width) // 2)
        y = wt + max(0, ((wb - wt) - self.height) // 2)

        self.window.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def _build_ui(self):
        # 1. Anchored bottom action bar
        action_bar = tk.Frame(self.window, bg=BG_MAIN, padx=20, pady=12)
        action_bar.pack(side=tk.BOTTOM, fill=tk.X)

        save_btn = tk.Button(
            action_bar,
            text="Save & Apply",
            font=(FONT_FAMILY, FONT_SIZE_BODY, "bold"),
            bg=GEMINI_BLUE,
            fg="#FFFFFF",
            activebackground="#4285F4",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=16,
            pady=6,
            cursor="hand2",
            command=self._save_and_close
        )
        save_btn.pack(side=tk.RIGHT, padx=(8, 0))

        apply_btn = tk.Button(
            action_bar,
            text="Apply",
            font=(FONT_FAMILY, FONT_SIZE_BODY),
            bg=BG_CARD,
            fg=TEXT_PRIMARY,
            activebackground=BG_HOVER,
            activeforeground=TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self._apply
        )
        apply_btn.pack(side=tk.RIGHT, padx=(8, 0))

        cancel_btn = tk.Button(
            action_bar,
            text="Cancel",
            font=(FONT_FAMILY, FONT_SIZE_BODY),
            bg=BG_CARD,
            fg=TEXT_SECONDARY,
            activebackground=BG_HOVER,
            activeforeground=TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self.window.destroy
        )
        cancel_btn.pack(side=tk.RIGHT)

        # 2. Scrollable Canvas
        canvas = tk.Canvas(self.window, bg=BG_MAIN, highlightthickness=0)
        v_scroll = DarkScrollbar(self.window, orient=tk.VERTICAL, command=canvas.yview)
        scroll_content = tk.Frame(canvas, bg=BG_MAIN, padx=20, pady=16)

        scroll_content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        cw = canvas.create_window((0, 0), window=scroll_content, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(cw, width=e.width))
        canvas.configure(yscrollcommand=v_scroll.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll.auto_pack(side=tk.RIGHT, fill=tk.Y)

        # Mouse wheel scrolling. A plain canvas.bind("<MouseWheel>", ...)
        # only fires when the cursor is directly over the canvas itself --
        # not over any of the labels/checkboxes/frames packed inside
        # scroll_content, which is what the cursor is actually over almost
        # all the time. bind_all while the cursor is anywhere over the
        # window (rebound on every <Enter>/<Leave>) makes the wheel work
        # regardless of which child widget is underneath it, and unbinding
        # on <Leave> keeps it from also scrolling this canvas while the
        # mouse is over some other window.
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

        # Header Title
        tk.Label(
            scroll_content,
            text="TwinRails Settings",
            font=(FONT_FAMILY, 14, "bold"),
            fg=TEXT_PRIMARY,
            bg=BG_MAIN
        ).pack(anchor="w", pady=(0, 14))

        # --- Section: Active Providers ---
        tk.Label(scroll_content, text="Active Providers", font=(FONT_FAMILY, FONT_SIZE_HEADING, "bold"), fg=TEXT_PRIMARY, bg=BG_MAIN).pack(anchor="w", pady=(0, 4))
        prov_box = tk.Frame(scroll_content, bg=BG_CARD, padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER_CARD)
        prov_box.pack(fill=tk.X, pady=(0, 16))

        self.prov_claude_var = tk.BooleanVar(value=("claude" in self.cfg_mgr.config.enabled_providers))
        self.prov_antigravity_var = tk.BooleanVar(value=("antigravity" in self.cfg_mgr.config.enabled_providers))

        tk.Checkbutton(prov_box, text="✱  Claude (claude.ai)", variable=self.prov_claude_var, font=(FONT_FAMILY, FONT_SIZE_BODY, "bold"), fg=CLAUDE_CORAL, bg=BG_CARD, selectcolor=BG_MAIN).pack(anchor="w", pady=2)
        tk.Checkbutton(prov_box, text="✦  Google Antigravity (Gemini & 3P)", variable=self.prov_antigravity_var, font=(FONT_FAMILY, FONT_SIZE_BODY, "bold"), fg=GEMINI_BLUE, bg=BG_CARD, selectcolor=BG_MAIN).pack(anchor="w", pady=2)

        # --- Section: Claude Credentials ---
        tk.Label(scroll_content, text="Claude Credentials", font=(FONT_FAMILY, FONT_SIZE_HEADING, "bold"), fg=TEXT_PRIMARY, bg=BG_MAIN).pack(anchor="w", pady=(0, 4))
        claude_box = tk.Frame(scroll_content, bg=BG_CARD, padx=14, pady=12, highlightthickness=1, highlightbackground=BORDER_CARD)
        claude_box.pack(fill=tk.X, pady=(0, 16))

        self.auth_mode_var = tk.StringVar(value=self.cfg_mgr.config.claude_auth_mode)
        for value, label in (
            ("auto", "Automatic: Claude Code sign-in, then cookie fallback"),
            ("oauth", "Claude Code sign-in only"),
            ("cookie", "Session cookie only"),
        ):
            tk.Radiobutton(
                claude_box, text=label, variable=self.auth_mode_var, value=value,
                font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD,
                selectcolor=BG_MAIN, anchor="w", command=self._update_auth_help
            ).pack(anchor="w", pady=1)

        # What the chosen route costs, shown as it is chosen -- the sign-in
        # route's two limits are not discoverable any other way until they bite.
        self.auth_help = tk.Label(
            claude_box, text="", font=(FONT_FAMILY, FONT_SIZE_CAPTION), fg=TEXT_MUTED,
            bg=BG_CARD, wraplength=420, justify="left", anchor="w"
        )
        self.auth_help.pack(fill=tk.X, pady=(4, 0))
        wrap_to_width(self.auth_help)
        self._update_auth_help()

        try:
            read_claude_code_token()
            token_status = "Claude Code token found. Use Test Connection to verify it."
        except ClaudeTokenExpired:
            token_status = "Claude Code's sign-in has expired. Run claude once to refresh it."
        except ClaudeTokenUnavailable:
            token_status = "No Claude Code token found on this machine."
        except ClaudeTokenReadError:
            token_status = "Claude Code credentials could not be read."
        tk.Label(
            claude_box, text=token_status, font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=TEXT_MUTED, bg=BG_CARD
        ).pack(anchor="w", pady=(4, 8))

        self.claude_key_label = tk.Label(
            claude_box, text="Session key (optional fallback):",
            font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_SECONDARY, bg=BG_CARD
        )
        self.claude_key_label.pack(anchor="w")
        cookie_help = tk.Label(
            claude_box,
            text="This is a full browser login credential (the sessionKey cookie from claude.ai). "
                 "Paste it for Cookie mode or as an Automatic fallback. "
                 "It is stored encrypted on this PC.",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION), fg=TEXT_MUTED, bg=BG_CARD,
            wraplength=420, justify="left", anchor="w"
        )
        cookie_help.pack(fill=tk.X, pady=(2, 4))
        wrap_to_width(cookie_help)
        self.auth_mode_var.trace_add("write", self._on_claude_auth_mode_changed)
        self._on_claude_auth_mode_changed()

        key_row = tk.Frame(claude_box, bg=BG_CARD)
        key_row.pack(fill=tk.X, pady=(4, 6))

        self.key_var = tk.StringVar(value=self.cfg_mgr.config.session_key)
        self.key_entry = tk.Entry(
            key_row,
            textvariable=self.key_var,
            font=(FONT_FAMILY, FONT_SIZE_BODY),
            bg="#141312",
            fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            relief=tk.FLAT,
            show="●",
            highlightthickness=1,
            highlightbackground=BORDER_CARD
        )
        self.key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4, padx=(0, 6))

        self.eye_btn = tk.Button(
            key_row,
            text="👁",
            font=(FONT_FAMILY, FONT_SIZE_BODY),
            bg=BG_CARD,
            fg=TEXT_SECONDARY,
            relief=tk.FLAT,
            padx=6,
            command=self._toggle_eye
        )
        self.eye_btn.pack(side=tk.LEFT, padx=(0, 4))

        paste_btn = tk.Button(
            key_row,
            text="Paste",
            font=(FONT_FAMILY, FONT_SIZE_BODY),
            bg=BG_CARD,
            fg=TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=8,
            command=self._paste_claude_key
        )
        paste_btn.pack(side=tk.LEFT)

        # Test Connection button for Claude
        test_row = tk.Frame(claude_box, bg=BG_CARD)
        test_row.pack(fill=tk.X, pady=(4, 0))

        self.test_btn = tk.Button(
            test_row,
            text="Test Claude Connection",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            bg=BG_CARD,
            fg=TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=10,
            pady=3,
            command=self._test_claude_connection
        )
        self.test_btn.pack(side=tk.LEFT)

        self.test_status_lbl = tk.Label(
            test_row, text="", font=(FONT_FAMILY, FONT_SIZE_CAPTION), fg=TEXT_MUTED,
            bg=BG_CARD, wraplength=260, justify="left", anchor="w"
        )
        self.test_status_lbl.pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)
        wrap_to_width(self.test_status_lbl)

        # --- Section: Antigravity Connection Status ---
        tk.Label(scroll_content, text="Antigravity Connection", font=(FONT_FAMILY, FONT_SIZE_HEADING, "bold"), fg=TEXT_PRIMARY, bg=BG_MAIN).pack(anchor="w", pady=(0, 4))
        ag_box = tk.Frame(scroll_content, bg=BG_CARD, padx=14, pady=12, highlightthickness=1, highlightbackground=BORDER_CARD)
        ag_box.pack(fill=tk.X, pady=(0, 16))

        is_ag_conn = False
        ag_port = 0
        if isinstance(self.current_state, UnifiedUsageState):
            ag_prov = self.current_state.providers.get("antigravity")
            if ag_prov and ag_prov.is_connected:
                is_ag_conn = True
                m = re.search(r"Port (\d+)", ag_prov.status_message)
                if m:
                    ag_port = int(m.group(1))

        status_text = f"🟢 Connected (Port {ag_port})" if is_ag_conn else "⚪ Auto-detected on localhost"
        tk.Label(ag_box, text=status_text, font=(FONT_FAMILY, FONT_SIZE_BODY, "bold"), fg=SUCCESS_GREEN if is_ag_conn else TEXT_SECONDARY, bg=BG_CARD).pack(anchor="w")
        ag_help = tk.Label(
            ag_box,
            text="Zero-configuration active: Port and CSRF token are auto-discovered from your running Antigravity IDE.",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=TEXT_MUTED,
            bg=BG_CARD,
            wraplength=420,
            justify="left",
            anchor="w"
        )
        ag_help.pack(fill=tk.X, pady=(2, 0))
        wrap_to_width(ag_help)

        # --- Section: Taskbar & Flyout Metrics Display ---
        tk.Label(scroll_content, text="Taskbar & Flyout Metrics Display", font=(FONT_FAMILY, FONT_SIZE_HEADING, "bold"), fg=TEXT_PRIMARY, bg=BG_MAIN).pack(anchor="w", pady=(0, 4))
        metrics_help = tk.Label(
            scroll_content,
            text="Choose which metrics to track and how they display. "
                 "Up to 4 can appear on the taskbar (more than 2 widens into a 2nd column).\n"
                 "• Full Bar: Progress bar + stats on taskbar and flyout.\n"
                 "• Compact: Stats only on taskbar (no bar, saves ~175px width) and flyout.\n"
                 "• Flyout only: Shown exclusively in the flyout popup, keeping the taskbar clean.\n"
                 "Claude model limits and credits (when available) already appear in the flyout. "
                 "Choose Full Bar or Compact here to add one to the taskbar.",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION), fg=TEXT_MUTED, bg=BG_MAIN, wraplength=420,
            justify="left", anchor="w"
        )
        metrics_help.pack(fill=tk.X, pady=(0, 4))
        wrap_to_width(metrics_help)
        metric_box = tk.Frame(scroll_content, bg=BG_CARD, padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER_CARD)
        metric_box.pack(fill=tk.X, pady=(0, 16))

        self.metric_vars = {}
        self.metric_mode_vars = {}
        self.metric_rows = {}
        configured = self.cfg_mgr.config.widget_metric_keys
        compact_configured = getattr(self.cfg_mgr.config, "compact_metric_keys", []) or []
        flyout_configured = getattr(self.cfg_mgr.config, "flyout_only_metric_keys", []) or []

        _legacy_key_aliases = {"five_hour": "claude_five_hour", "seven_day": "claude_seven_day"}
        # The old parser gave every scoped model the same key. It could only
        # resolve the first one, so keep that exact choice when upgrading.
        if self.current_state is not None:
            first_scoped = next(
                (m.key for m in self.current_state.all_metrics
                 if m.provider_id == "claude" and m.key.startswith("limits_weekly_scoped_")),
                None,
            )
            if first_scoped:
                _legacy_key_aliases["limits_weekly_scoped"] = first_scoped
        resolved_configured = [_legacy_key_aliases.get(k, k) for k in configured]
        resolved_compact = set([_legacy_key_aliases.get(k, k) for k in compact_configured])
        resolved_flyout = set([_legacy_key_aliases.get(k, k) for k in flyout_configured])

        self._choices = _metric_choices(
            self.current_state,
            [*resolved_configured, *sorted(resolved_compact), *sorted(resolved_flyout)],
        )
        valid_keys = {key for key, _ in self._choices}
        self._metric_order = [k for k in resolved_configured if k in valid_keys]
        self._compact_keys = set([k for k in resolved_compact if k in valid_keys])
        self._flyout_only_keys = set([k for k in resolved_flyout if k in valid_keys])

        for key, label in self._choices:
            is_active = (key in self._metric_order) or (key in self._flyout_only_keys)
            if key in self._flyout_only_keys:
                init_mode = "Flyout only"
            elif key in self._compact_keys:
                init_mode = "Compact"
            else:
                init_mode = "Full Bar"

            var = tk.BooleanVar(value=is_active)
            self.metric_vars[key] = var
            mode_var = tk.StringVar(value=init_mode)
            self.metric_mode_vars[key] = mode_var

            row = tk.Frame(metric_box, bg=BG_CARD)
            row.pack(fill=tk.X, pady=2)

            cb = tk.Checkbutton(
                row,
                text=label,
                variable=var,
                font=(FONT_FAMILY, FONT_SIZE_BODY),
                fg=TEXT_PRIMARY,
                bg=BG_CARD,
                selectcolor=BG_MAIN,
                command=lambda k=key: self._on_metric_toggled(k)
            )
            cb.pack(side=tk.LEFT)

            pos_lbl = tk.Label(row, text="", font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"), fg=TEXT_MUTED, bg=BG_CARD, width=2)
            pos_lbl.pack(side=tk.RIGHT, padx=(4, 0))
            down_btn = tk.Label(row, text="▼", font=(FONT_FAMILY, FONT_SIZE_CAPTION), bg=BG_CARD)
            down_btn.pack(side=tk.RIGHT, padx=(2, 0))
            down_btn.bind("<Button-1>", lambda e, k=key: self._move_metric(k, 1))
            up_btn = tk.Label(row, text="▲", font=(FONT_FAMILY, FONT_SIZE_CAPTION), bg=BG_CARD)
            up_btn.pack(side=tk.RIGHT, padx=(2, 0))
            up_btn.bind("<Button-1>", lambda e, k=key: self._move_metric(k, -1))

            om = tk.OptionMenu(
                row, mode_var, "Full Bar", "Compact", "Flyout only",
                command=lambda val, k=key: self._on_mode_changed(k, val)
            )
            om.configure(
                bg=BG_MAIN, fg=TEXT_PRIMARY, activebackground=BG_HOVER, activeforeground=TEXT_PRIMARY,
                relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER_CARD,
                font=(FONT_FAMILY, FONT_SIZE_CAPTION), padx=6, pady=1
            )
            menu = om["menu"]
            menu.configure(
                bg=BG_CARD, fg=TEXT_PRIMARY, activebackground=GEMINI_BLUE, activeforeground="#FFFFFF",
                relief=tk.FLAT, font=(FONT_FAMILY, FONT_SIZE_BODY)
            )
            om.pack(side=tk.RIGHT, padx=(6, 4))

            self.metric_rows[key] = {
                "cb": cb, "om": om, "pos_lbl": pos_lbl,
                "up_btn": up_btn, "down_btn": down_btn
            }

        self._refresh_metric_order_ui()

        # --- Section: Pacing Color Mode ---
        tk.Label(scroll_content, text="Pacing Color Mode", font=(FONT_FAMILY, FONT_SIZE_HEADING, "bold"), fg=TEXT_PRIMARY, bg=BG_MAIN).pack(anchor="w", pady=(0, 4))
        pacing_box = tk.Frame(scroll_content, bg=BG_CARD, padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER_CARD)
        pacing_box.pack(fill=tk.X, pady=(0, 16))

        self.pacing_mode_var = tk.StringVar(value=self.cfg_mgr.config.pacing_color_mode)
        r1 = tk.Radiobutton(pacing_box, text="Dynamic Halfway Midpoint (Recommended)", variable=self.pacing_mode_var, value="dynamic", font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_MAIN)
        r1.pack(anchor="w")
        tk.Label(pacing_box, text="Amber when past elapsed marker; Red halfway between marker and 100%.", font=(FONT_FAMILY, FONT_SIZE_CAPTION), fg=TEXT_MUTED, bg=BG_CARD).pack(anchor="w", padx=(20, 0), pady=(0, 4))

        r2 = tk.Radiobutton(pacing_box, text="Fixed Delta (e.g. +10% above elapsed)", variable=self.pacing_mode_var, value="fixed", font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_MAIN)
        r2.pack(anchor="w")

        # --- Section: Percentage Display ---
        tk.Label(scroll_content, text="Percentage Display Mode", font=(FONT_FAMILY, FONT_SIZE_HEADING, "bold"), fg=TEXT_PRIMARY, bg=BG_MAIN).pack(anchor="w", pady=(0, 4))
        pct_box = tk.Frame(scroll_content, bg=BG_CARD, padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER_CARD)
        pct_box.pack(fill=tk.X, pady=(0, 16))

        self.pct_mode_var = tk.StringVar(value=self.cfg_mgr.config.percentage_display_mode)
        tk.Radiobutton(pct_box, text="Show % Utilized (e.g. 45% used)", variable=self.pct_mode_var, value="used", font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_MAIN).pack(anchor="w")
        tk.Radiobutton(pct_box, text="Show % Remaining (e.g. 55% remaining)", variable=self.pct_mode_var, value="remaining", font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_MAIN).pack(anchor="w")

        # --- Section: Taskbar Bar Appearance ---
        tk.Label(scroll_content, text="Taskbar Bar Appearance", font=(FONT_FAMILY, FONT_SIZE_HEADING, "bold"), fg=TEXT_PRIMARY, bg=BG_MAIN).pack(anchor="w", pady=(0, 4))
        appearance_help = tk.Label(
            scroll_content,
            text="The bar is transparent by default: it paints nothing behind itself, so it "
                 "matches your taskbar exactly, and its text follows Windows' light or dark "
                 "setting on its own. Untick transparency to paint a solid panel instead; "
                 "you can then choose its color for a dark and for a light taskbar.",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION), fg=TEXT_MUTED, bg=BG_MAIN, wraplength=420,
            justify="left", anchor="w"
        )
        appearance_help.pack(fill=tk.X, pady=(0, 4))
        wrap_to_width(appearance_help)
        appearance_box = tk.Frame(scroll_content, bg=BG_CARD, padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER_CARD)
        appearance_box.pack(fill=tk.X, pady=(0, 16))

        # There is no light/dark choice any more: the bar follows Windows'
        # own taskbar setting (taskbar_bar._configured_bg_color). The two
        # colors only matter for a solid panel, so they only show without
        # transparency -- see docs/bar-colors.md.
        self.bar_transparent_var = tk.BooleanVar(value=getattr(self.cfg_mgr.config, "bar_transparent_bg", True))
        self._transparent_check = tk.Checkbutton(
            appearance_box,
            text="Transparent background (show the real taskbar through the bar)",
            variable=self.bar_transparent_var,
            font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_MAIN,
            wraplength=400, justify="left", anchor="w",
            command=self._update_panel_colors_visibility
        )
        self._transparent_check.pack(anchor="w")

        self.bar_color_dark_var = tk.StringVar(value=self.cfg_mgr.config.bar_bg_color_dark)
        self.bar_color_light_var = tk.StringVar(value=self.cfg_mgr.config.bar_bg_color_light)
        self._bar_color_swatches = {}
        self._panel_colors = tk.Frame(appearance_box, bg=BG_CARD)
        self._make_color_row(self._panel_colors, "On a dark taskbar:", self.bar_color_dark_var)
        self._make_color_row(self._panel_colors, "On a light taskbar:", self.bar_color_light_var)
        self._update_panel_colors_visibility()

        self.show_time_divisions_var = tk.BooleanVar(
            value=self.cfg_mgr.config.show_time_divisions
        )
        tk.Checkbutton(
            appearance_box,
            text="Show hour/day divisions (taskbar and flyout)",
            variable=self.show_time_divisions_var,
            font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD,
            selectcolor=BG_MAIN, anchor="w"
        ).pack(anchor="w", pady=(8, 0))

        # --- Section: Polling Interval ---
        tk.Label(scroll_content, text="Refresh Interval", font=(FONT_FAMILY, FONT_SIZE_HEADING, "bold"), fg=TEXT_PRIMARY, bg=BG_MAIN).pack(anchor="w", pady=(0, 4))
        poll_box = tk.Frame(scroll_content, bg=BG_CARD, padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER_CARD)
        poll_box.pack(fill=tk.X, pady=(0, 16))

        self.poll_var = tk.IntVar(value=max(60, self.cfg_mgr.config.poll_interval_seconds))
        for sec, txt in [(60, "60 seconds"), (120, "2 minutes"), (300, "5 minutes (default)")]:
            tk.Radiobutton(poll_box, text=txt, variable=self.poll_var, value=sec, font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_MAIN).pack(anchor="w")
        poll_help = tk.Label(
            poll_box,
            text="Claude through the Claude Code sign-in updates at most every 5 minutes, "
                 "whatever you pick here. The session cookie and Gemini follow this setting.",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION), fg=TEXT_MUTED, bg=BG_CARD,
            wraplength=420, justify="left", anchor="w"
        )
        poll_help.pack(fill=tk.X, pady=(4, 0))
        wrap_to_width(poll_help)

        # --- Section: General Options ---
        tk.Label(scroll_content, text="General Options", font=(FONT_FAMILY, FONT_SIZE_HEADING, "bold"), fg=TEXT_PRIMARY, bg=BG_MAIN).pack(anchor="w", pady=(0, 4))
        gen_box = tk.Frame(scroll_content, bg=BG_CARD, padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER_CARD)
        gen_box.pack(fill=tk.X, pady=(0, 16))

        self.autostart_var = tk.BooleanVar(value=self.cfg_mgr.config.start_with_windows)
        tk.Checkbutton(gen_box, text="Start automatically with Windows", variable=self.autostart_var, font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_MAIN).pack(anchor="w", pady=2)

        self.show_bar_var = tk.BooleanVar(value=self.cfg_mgr.config.show_docked_bar)
        tk.Checkbutton(gen_box, text="Show compact Taskbar Bar docked in taskbar", variable=self.show_bar_var, font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_MAIN).pack(anchor="w", pady=2)

        self.lock_bar_var = tk.BooleanVar(value=self.cfg_mgr.config.docked_bar_locked)
        tk.Checkbutton(gen_box, text="Lock taskbar bar position", variable=self.lock_bar_var, font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_MAIN).pack(anchor="w", pady=2)

    _AUTH_HELP = {
        "auto": ("Uses your Claude Code sign-in while it works, and the session cookie "
                 "below when it doesn't (expired, refused or rate limited). Sign-in "
                 "readings update at most every 5 minutes.", TEXT_MUTED),
        "oauth": ("Heads-up: no cookie needed, but the sign-in expires about 8 hours "
                  "after Claude Code last ran -- run claude to renew it -- and Anthropic "
                  "rate-limits this route, so Claude updates at most every 5 minutes, "
                  "whatever the refresh interval.", WARNING_AMBER),
        "cookie": ("Fastest: follows the refresh interval, even 60 seconds, with no "
                   "8-hour sign-in to renew. Needs the session key below, which is a full "
                   "login credential.", TEXT_MUTED),
    }

    def _update_auth_help(self):
        text, color = self._AUTH_HELP.get(self.auth_mode_var.get(), ("", TEXT_MUTED))
        self.auth_help.configure(text=text, fg=color)

    def _update_panel_colors_visibility(self):
        # after= keeps the colors directly under the checkbox; a bare pack()
        # after pack_forget() would append them below the divisions option.
        if self.bar_transparent_var.get():
            self._panel_colors.pack_forget()
        else:
            self._panel_colors.pack(fill=tk.X, pady=(6, 0), after=self._transparent_check)

    def _make_color_row(self, parent: tk.Widget, label_text: str, var: tk.StringVar):
        row = tk.Frame(parent, bg=BG_CARD)
        row.pack(fill=tk.X, pady=2)

        tk.Label(row, text=label_text, font=(FONT_FAMILY, FONT_SIZE_BODY), fg=TEXT_SECONDARY, bg=BG_CARD, width=17, anchor="w").pack(side=tk.LEFT)

        swatch = tk.Label(row, text="", bg=self._safe_hex(var.get()), width=3, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER_CARD)
        swatch.pack(side=tk.LEFT, padx=(0, 6), ipady=4)
        self._bar_color_swatches[label_text] = swatch

        entry = tk.Entry(
            row,
            textvariable=var,
            font=(FONT_FAMILY, FONT_SIZE_BODY),
            bg="#141312",
            fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            relief=tk.FLAT,
            width=10,
            highlightthickness=1,
            highlightbackground=BORDER_CARD
        )
        entry.pack(side=tk.LEFT, ipady=4)

        # Live swatch preview as the user types/pastes a hex code -- only
        # updates the swatch on a value that Tk will actually accept as a
        # color; an in-progress or invalid hex just leaves the last good
        # swatch showing instead of throwing on every keystroke.
        def _on_change(*_args):
            hex_val = var.get().strip()
            if not hex_val.startswith("#"):
                hex_val = f"#{hex_val}"
            try:
                swatch.configure(bg=hex_val)
            except tk.TclError:
                pass

        var.trace_add("write", _on_change)
        return row

    @staticmethod
    def _safe_hex(value: str, fallback: str = "#1C1B1A") -> str:
        v = value.strip()
        if not v.startswith("#"):
            v = f"#{v}"
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
            return v
        return fallback

    def _toggle_eye(self):
        self._show_key = not self._show_key
        self.key_entry.configure(show="" if self._show_key else "●")

    def _paste_claude_key(self):
        try:
            val = self.window.clipboard_get()
            clean = clean_cookie_val(val, "sessionKey")
            self.key_var.set(clean)
        except Exception:
            pass

    def _on_claude_auth_mode_changed(self, *_args):
        mode = self.auth_mode_var.get()
        if mode == "cookie":
            label = "Session key (required for cookie mode):"
        elif mode == "oauth":
            label = "Session key (unused in Claude Code mode):"
        else:
            label = "Session key (optional fallback):"
        self.claude_key_label.configure(text=label)

    def _test_claude_connection(self):
        key = self.key_var.get().strip()
        mode = self.auth_mode_var.get()
        if mode == "cookie" and not key:
            self.test_status_lbl.configure(text="Please enter a sessionKey first", fg=WARNING_AMBER)
            return

        self.test_btn.configure(state=tk.DISABLED)
        self.test_status_lbl.configure(text="Connecting...", fg=TEXT_MUTED)

        def _worker():
            try:
                client = ClaudeClient(
                    session_key=key, org_id=self.cfg_mgr.config.org_id, auth_mode=mode
                )
                state = client.fetch_usage()
                def _done():
                    self.test_btn.configure(state=tk.NORMAL)
                    if state.error_message:
                        self.test_status_lbl.configure(text=f"Failed: {state.error_message}", fg=WARNING_AMBER)
                    else:
                        self.test_status_lbl.configure(text="✓ Connected successfully!", fg=SUCCESS_GREEN)
                self.window.after(0, _done)
            except Exception:
                def _err():
                    self.test_btn.configure(state=tk.NORMAL)
                    self.test_status_lbl.configure(text="Unexpected connection test error.", fg=WARNING_AMBER)
                self.window.after(0, _err)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_metric_toggled(self, key: str):
        checked = self.metric_vars[key].get()
        if checked:
            mode = self.metric_mode_vars[key].get()
            if mode == "Flyout only":
                self._flyout_only_keys.add(key)
                if key in self._metric_order:
                    self._metric_order.remove(key)
            else:
                if key not in self._metric_order:
                    if len(self._metric_order) >= MAX_WIDGET_METRICS:
                        # Taskbar slots are full, so switch this newly checked metric to Flyout only
                        self.metric_mode_vars[key].set("Flyout only")
                        self._flyout_only_keys.add(key)
                    else:
                        self._metric_order.append(key)
                        self._flyout_only_keys.discard(key)
        else:
            if key in self._metric_order:
                self._metric_order.remove(key)
            self._flyout_only_keys.discard(key)
            self._compact_keys.discard(key)

        self._refresh_metric_order_ui()

    def _on_mode_changed(self, key: str, new_mode: str):
        # Setting a mode enables the metric if not already checked
        self.metric_vars[key].set(True)
        self.metric_mode_vars[key].set(new_mode)

        if new_mode == "Flyout only":
            if key in self._metric_order:
                self._metric_order.remove(key)
            self._flyout_only_keys.add(key)
            self._compact_keys.discard(key)
        elif new_mode == "Compact":
            self._flyout_only_keys.discard(key)
            self._compact_keys.add(key)
            if key not in self._metric_order:
                if len(self._metric_order) < MAX_WIDGET_METRICS:
                    self._metric_order.append(key)
                else:
                    self.metric_mode_vars[key].set("Flyout only")
                    self._flyout_only_keys.add(key)
                    self._compact_keys.discard(key)
        elif new_mode == "Full Bar":
            self._flyout_only_keys.discard(key)
            self._compact_keys.discard(key)
            if key not in self._metric_order:
                if len(self._metric_order) < MAX_WIDGET_METRICS:
                    self._metric_order.append(key)
                else:
                    self.metric_mode_vars[key].set("Flyout only")
                    self._flyout_only_keys.add(key)

        self._refresh_metric_order_ui()

    def _move_metric(self, key: str, direction: int):
        if key not in self._metric_order:
            return
        i = self._metric_order.index(key)
        j = i + direction
        if 0 <= j < len(self._metric_order):
            self._metric_order[i], self._metric_order[j] = self._metric_order[j], self._metric_order[i]
        self._refresh_metric_order_ui()

    def _refresh_metric_order_ui(self):
        for key, widgets in self.metric_rows.items():
            is_checked = self.metric_vars[key].get()
            mode = self.metric_mode_vars[key].get()
            om = widgets["om"]

            if not is_checked:
                widgets["pos_lbl"].configure(text="")
                widgets["up_btn"].configure(fg=BORDER_CARD, cursor="arrow")
                widgets["down_btn"].configure(fg=BORDER_CARD, cursor="arrow")
                om.configure(state=tk.DISABLED)
                continue

            om.configure(state=tk.NORMAL)

            if mode == "Flyout only" or key not in self._metric_order:
                widgets["pos_lbl"].configure(text="")
                widgets["up_btn"].configure(fg=BORDER_CARD, cursor="arrow")
                widgets["down_btn"].configure(fg=BORDER_CARD, cursor="arrow")
            else:
                pos = self._metric_order.index(key) + 1
                widgets["pos_lbl"].configure(text=str(pos))
                can_up = pos > 1
                can_down = pos < len(self._metric_order)
                widgets["up_btn"].configure(
                    fg=(TEXT_SECONDARY if can_up else BORDER_CARD),
                    cursor=("hand2" if can_up else "arrow")
                )
                widgets["down_btn"].configure(
                    fg=(TEXT_SECONDARY if can_down else BORDER_CARD),
                    cursor=("hand2" if can_down else "arrow")
                )

    def _save_and_close(self):
        self._apply()
        self.window.destroy()

    def _apply(self):
        enabled_provs = []
        if self.prov_claude_var.get():
            enabled_provs.append("claude")
        if self.prov_antigravity_var.get():
            enabled_provs.append("antigravity")
        if not enabled_provs:
            enabled_provs = ["claude", "antigravity"]

        selected_taskbar = [
            k for k in self._metric_order
            if self.metric_vars[k].get() and self.metric_mode_vars[k].get() != "Flyout only"
        ]
        selected_compact = [
            k for k in selected_taskbar
            if self.metric_mode_vars[k].get() == "Compact"
        ]
        selected_flyout_only = [
            k for k in self._flyout_only_keys
            if self.metric_vars[k].get() and self.metric_mode_vars[k].get() == "Flyout only"
        ]

        if not selected_taskbar and not selected_flyout_only:
            selected_taskbar = ["claude_five_hour", "gemini_5h"]

        cfg = self.cfg_mgr.config
        cfg.enabled_providers = enabled_provs
        cfg.claude_auth_mode = self.auth_mode_var.get()
        cfg.session_key = self.key_var.get().strip()
        cfg.widget_metric_keys = selected_taskbar[:MAX_WIDGET_METRICS]
        cfg.compact_metric_keys = selected_compact
        cfg.flyout_only_metric_keys = selected_flyout_only
        cfg.pacing_color_mode = self.pacing_mode_var.get()
        cfg.percentage_display_mode = self.pct_mode_var.get()
        cfg.bar_bg_color_dark = self._safe_hex(self.bar_color_dark_var.get(), cfg.bar_bg_color_dark)
        cfg.bar_bg_color_light = self._safe_hex(self.bar_color_light_var.get(), cfg.bar_bg_color_light)
        cfg.bar_transparent_bg = bool(self.bar_transparent_var.get())
        cfg.show_time_divisions = bool(self.show_time_divisions_var.get())
        cfg.poll_interval_seconds = self.poll_var.get()
        cfg.show_docked_bar = self.show_bar_var.get()
        cfg.docked_bar_locked = self.lock_bar_var.get()

        if cfg.start_with_windows != self.autostart_var.get():
            self.cfg_mgr.toggle_startup(self.autostart_var.get())

        self.cfg_mgr.save(cfg)
        self.on_save(cfg)

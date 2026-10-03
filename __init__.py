"""Todoist plugin for FiestaBoard.

Shows how many tasks are due today, how many are overdue, and the next task
to do, from the user's Todoist account.

Two ways to connect, side by side:

* **Sign in with Todoist** (the manifest's ``oauth`` block). FiestaBoard is a
  public client: its client ID is the URL of a client ID metadata document
  (``https://fiestaboard.app/auth/clients/todoist.json``), so nobody registers
  an app and there is no client secret. The platform runs the PKCE flow,
  stores the tokens and refreshes them (Todoist access tokens last an hour
  and refresh tokens rotate on every refresh). This plugin only calls
  ``self.get_oauth_token()``.
* **A personal API token** pasted into the ``api_key`` setting. Used only
  when there is no sign-in.

Endpoint (Todoist API v1): ``GET /api/v1/tasks/filter?query=today | overdue``,
paginated with ``cursor`` / ``next_cursor``.
"""

import logging
import re
import threading
import time
import unicodedata
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from src.board_chars import BoardChars
from src.plugins.base import PluginBase, PluginResult
from src.text_to_board import take_tiles

logger = logging.getLogger(__name__)

FILTER_URL = "https://api.todoist.com/api/v1/tasks/filter"
FILTER_QUERY = "today | overdue"
USER_AGENT = "FiestaBoard Todoist Plugin (https://github.com/Fiestaboard/fiestaboard-plugin--todoist)"
REQUEST_TIMEOUT_SECONDS = 10
PAGE_LIMIT = 200  # the API's maximum
MAX_PAGES = 5
MAX_LISTED_TASKS = 10

TRANSIENT_COOLDOWN_SECONDS = 30
AUTH_COOLDOWN_SECONDS = 60
DEFAULT_RETRY_AFTER_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 3600
STALE_DATA_LIMIT_SECONDS = 2 * 3600

COLS = 22
TILE_OVERDUE = "{63}"  # red
TILE_TODAY = "{65}"  # yellow
TILE_CLEAR = "{66}"  # green

NOT_CONNECTED_ERROR = (
    "Not connected to Todoist. Open this plugin's settings and sign in with Todoist, or paste a personal API token."
)
SIGN_IN_REJECTED_ERROR = "Todoist rejected the sign-in (401). Open this plugin's settings and press Reconnect."
TOKEN_REJECTED_ERROR = (
    "Todoist rejected the personal API token (401). Copy it again from Todoist > Settings > Integrations > Developer."
)
FORBIDDEN_ERROR = "Todoist refused access to your tasks (403). Reconnect and allow FiestaBoard to read your tasks."

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"[*_`~]+")


class TodoistError(Exception):
    """A failed request. ``transient`` errors may show the last good data."""

    def __init__(self, message: str, *, transient: bool, cooldown: float):
        super().__init__(message)
        self.transient = transient
        self.cooldown = cooldown


def _monotonic() -> float:
    return time.monotonic()


def _now(tz: ZoneInfo | None = None) -> datetime:
    return datetime.now(tz)


def board_safe(text: Any) -> str:
    """Uppercase *text* and keep only characters the board can show."""
    folded = unicodedata.normalize("NFKD", str(text or ""))
    kept = []
    for ch in folded.upper():
        if ch.isspace():
            kept.append(" ")
        elif BoardChars.get_char_code(ch) is not None:
            kept.append(ch)
    return re.sub(r" {2,}", " ", "".join(kept)).strip()


def plain_text(content: str) -> str:
    """Todoist task names are Markdown: keep link text, drop emphasis marks."""
    return _MD_MARKS.sub("", _MD_LINK.sub(r"\1", content or ""))


def fit(text: str, width: int) -> str:
    return take_tiles(text, width)[0].rstrip()


def priority_label(priority: Any) -> str:
    """API priority 4 (urgent) is what Todoist's apps show as P1."""
    try:
        value = int(priority)
    except (TypeError, ValueError):
        return "P4"
    return f"P{5 - value}" if 1 <= value <= 4 else "P4"


def format_time(when: datetime) -> str:
    hour = when.hour % 12 or 12
    return f"{hour}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"


def _parse_due(due: Any, tz: ZoneInfo | None) -> tuple[date, datetime | None] | None:
    """``(day, time)`` of a due object in the board's zone; time is None for all-day tasks."""
    if not isinstance(due, dict):
        return None
    raw = str(due.get("datetime") or due.get("date") or "")
    try:
        if "T" not in raw:
            return date.fromisoformat(raw), None
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone(tz)  # tz None: the board's local zone
    return when.date(), when


class TodoistPlugin(PluginBase):
    """Tasks due today and overdue, from the user's Todoist account."""

    def __init__(self, manifest: dict[str, Any]):
        super().__init__(manifest)
        self._lock = threading.RLock()
        self._reset_state()

    def _reset_state(self) -> None:
        self._last_good: list[dict[str, Any]] | None = None
        self._last_good_at = 0.0
        self._cooldown_until = 0.0
        self._cooldown_error: TodoistError | None = None
        self._cooldown_token: str | None = None

    @property
    def plugin_id(self) -> str:
        return "todoist"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        tz_name = str(config.get("timezone") or "").strip()
        if tz_name and self._zone(tz_name) is None:
            errors.append(f"Unknown time zone: {tz_name}. Use a name like America/New_York, or leave it empty.")
        errors.extend(self._validate_refresh_seconds(config))
        return errors

    def on_config_change(self, old_config: dict[str, Any], new_config: dict[str, Any]) -> None:
        super().on_config_change(old_config, new_config)
        with self._lock:
            self._reset_state()

    @staticmethod
    def _zone(name: str) -> ZoneInfo | None:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return None

    def _credential(self) -> tuple[str | None, bool]:
        """``(token, from_sign_in)``. A sign-in wins over a pasted API token."""
        get_token = getattr(self, "get_oauth_token", None)
        if callable(get_token):
            try:
                token = get_token()
            except Exception as exc:
                logger.warning("Todoist: could not read the sign-in: %s", exc)
                token = None
            if token:
                return token, True
        api_key = str(self.config.get("api_key") or "").strip()
        return (api_key or None), False

    # ------------------------------------------------------------------
    # Todoist requests
    # ------------------------------------------------------------------

    def _get_page(self, token: str, from_sign_in: bool, cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"query": FILTER_QUERY, "limit": PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        try:
            response = requests.get(
                FILTER_URL,
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.Timeout:
            raise TodoistError("Timed out contacting Todoist", transient=True, cooldown=TRANSIENT_COOLDOWN_SECONDS)
        except requests.exceptions.RequestException as exc:
            raise TodoistError(
                f"Could not reach Todoist: {type(exc).__name__}", transient=True, cooldown=TRANSIENT_COOLDOWN_SECONDS
            )

        status = response.status_code
        if status == 200:
            try:
                body = response.json()
            except ValueError:
                body = None
            if not isinstance(body, dict) or not isinstance(body.get("results"), list):
                raise TodoistError(
                    "Unexpected response from Todoist", transient=True, cooldown=TRANSIENT_COOLDOWN_SECONDS
                )
            return body
        if status == 401:
            message = SIGN_IN_REJECTED_ERROR if from_sign_in else TOKEN_REJECTED_ERROR
            raise TodoistError(message, transient=False, cooldown=0)
        if status == 403:
            raise TodoistError(FORBIDDEN_ERROR, transient=False, cooldown=AUTH_COOLDOWN_SECONDS)
        if status == 429:
            wait = self._retry_after(response)
            raise TodoistError(
                f"Todoist rate limit reached; trying again in {int(wait)}s", transient=True, cooldown=wait
            )
        if status >= 500:
            raise TodoistError(
                f"Todoist is having trouble (HTTP {status}); will retry",
                transient=True,
                cooldown=TRANSIENT_COOLDOWN_SECONDS,
            )
        raise TodoistError(
            f"Unexpected response from Todoist (HTTP {status})", transient=False, cooldown=TRANSIENT_COOLDOWN_SECONDS
        )

    @staticmethod
    def _retry_after(response: requests.Response) -> float:
        try:
            wait = float(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS))
        except (TypeError, ValueError):
            wait = DEFAULT_RETRY_AFTER_SECONDS
        return min(max(wait, 1.0), MAX_RETRY_AFTER_SECONDS)

    def _load_tasks(self, token: str, from_sign_in: bool) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            body = self._get_page(token, from_sign_in, cursor)
            tasks.extend(t for t in body["results"] if isinstance(t, dict))
            cursor = body.get("next_cursor")
            if not cursor:
                break
        return tasks

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def fetch_data(self) -> PluginResult:
        try:
            token, from_sign_in = self._credential()
            if not token:
                return PluginResult(available=False, error=NOT_CONNECTED_ERROR)

            with self._lock:
                try:
                    if (
                        self._cooldown_error is not None
                        and self._cooldown_token == token  # a new sign-in or token is tried at once
                        and _monotonic() < self._cooldown_until
                    ):
                        raise self._cooldown_error
                    try:
                        tasks = self._load_tasks(token, from_sign_in)
                    except TodoistError as exc:
                        if exc.cooldown > 0:
                            self._cooldown_until = _monotonic() + exc.cooldown
                            self._cooldown_error = exc
                            self._cooldown_token = token
                        raise
                except TodoistError as exc:
                    stale = self._stale_tasks() if exc.transient else None
                    if stale is not None:
                        logger.info("Todoist: %s; showing the last data", exc)
                        tasks = stale
                    else:
                        logger.warning("Todoist: %s", exc)
                        return PluginResult(available=False, error=str(exc))
                else:
                    self._cooldown_error = None
                    self._last_good = tasks
                    self._last_good_at = _monotonic()

            data = self._build_data(tasks)
            return PluginResult(available=True, data=data, formatted_lines=self._format_display(data))
        except Exception as exc:  # fetch_data must never raise
            logger.exception("Error fetching Todoist data")
            return PluginResult(available=False, error=str(exc))

    def _stale_tasks(self) -> list[dict[str, Any]] | None:
        if self._last_good is None or _monotonic() - self._last_good_at > STALE_DATA_LIMIT_SECONDS:
            return None
        return self._last_good

    # ------------------------------------------------------------------
    # Variables
    # ------------------------------------------------------------------

    def _build_data(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        tz_name = str(self.config.get("timezone") or "").strip()
        tz = self._zone(tz_name) if tz_name else None
        today = _now(tz).date()

        entries = []
        for item in tasks:
            parsed = _parse_due(item.get("due"), tz)
            if parsed is None or parsed[0] > today:
                continue
            day, when = parsed
            overdue = day < today
            label = "OVERDUE" if overdue else (format_time(when) if when else "TODAY")
            try:
                priority = int(item.get("priority") or 1)
            except (TypeError, ValueError):
                priority = 1
            sort_key = (
                day,
                0 if when else 1,
                when.strftime("%H%M") if when else "",
                -priority,
                item.get("day_order") or 0,
                item.get("child_order") or 0,
            )
            entries.append((sort_key, overdue, label, item))
        entries.sort(key=lambda e: e[0])

        overdue_count = sum(1 for e in entries if e[1])
        today_count = len(entries) - overdue_count
        listed = [
            {
                "content": fit(board_safe(plain_text(item.get("content", ""))), COLS),
                "due": label,
                "priority": priority_label(item.get("priority")),
            }
            for _, _, label, item in entries[:MAX_LISTED_TASKS]
        ]
        first = listed[0] if listed else {"content": "", "due": "", "priority": ""}

        parts = []
        if today_count:
            parts.append(f"{today_count} TODAY")
        if overdue_count:
            parts.append(f"{overdue_count} OVERDUE")
        if overdue_count:
            tile = TILE_OVERDUE
        elif today_count:
            tile = TILE_TODAY
        else:
            tile = TILE_CLEAR

        return {
            "due_today": today_count,
            "overdue": overdue_count,
            "due_total": len(entries),
            "summary": " ".join(parts) or "ALL CLEAR",
            "status_tile": tile,
            "all_clear": not entries,
            "next_task": first["content"],
            "next_task_due": first["due"],
            "next_task_priority": first["priority"],
            "tasks": listed,
        }

    @staticmethod
    def _format_display(data: dict[str, Any]) -> list[str]:
        return [
            f"{data['status_tile']} TODOIST",
            "",
            data["summary"],
            "",
            "NEXT UP" if data["next_task"] else "",
            data["next_task"],
        ]


# Export hook: the loader looks for a module-level `Plugin`.
Plugin = TodoistPlugin

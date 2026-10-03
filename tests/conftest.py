"""Shared fixtures for the Todoist plugin tests.

``todoist_api`` patches ``requests.get`` with a fake Todoist API v1 that
answers ``GET /api/v1/tasks/filter`` with pages shaped like the real ones
(``{"results": [...], "next_cursor": ...}``). Tests set its tasks, page size,
or queue raw responses to simulate errors. Nothing here touches the network.

``get_oauth_token`` is patched on the plugin instance: the platform's OAuth
service is not under test here, only how the plugin uses what it returns.

All task names are invented.
"""

import json
from collections import deque
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from plugins.todoist import TodoistPlugin

MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"
FILTER_URL = "https://api.todoist.com/api/v1/tasks/filter"
TEST_TOKEN = "test_oauth_access_token"
TEST_API_KEY = "test_personal_api_token"
TODAY = "2026-10-02"
YESTERDAY = "2026-10-01"
LAST_WEEK = "2026-09-25"
NOW = datetime(2026, 10, 2, 9, 0, 0)  # naive: the board's local time


def task(content, date=TODAY, *, time=None, priority=1, day_order=0, child_order=1, task_id=None):
    """A task shaped like ``ItemSyncView`` from the Todoist API v1."""
    due = None
    if date is not None:
        due = {"date": date, "is_recurring": False, "lang": "en", "string": "today", "timezone": None}
        if time is not None:
            due["date"] = f"{date}T{time}"
    return {
        "id": task_id or f"task_{abs(hash(content)) % 10**8}",
        "content": content,
        "priority": priority,
        "day_order": day_order,
        "child_order": child_order,
        "due": due,
        "checked": False,
        "is_deleted": False,
    }


class FakeResponse:
    def __init__(self, body=None, status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeTodoist:
    """Answers requests.get the way GET /api/v1/tasks/filter would."""

    def __init__(self):
        self.tasks = [
            task("Call the plumber", YESTERDAY, priority=4),
            task("Water the ferns", TODAY, time="08:30:00", priority=1),
            task("Renew library books", TODAY, priority=3),
            task("Pack gym bag", TODAY, priority=1, day_order=2),
        ]
        self.page_size = 200
        self.overrides = deque()
        self.calls = []

    def fail(self, response=None, *, status=None, headers=None, times=1):
        if response is None:
            body = {"error": "test", "error_tag": "TEST", "http_code": status}
            response = FakeResponse(body, status, headers)
        self.overrides.extend([response] * times)

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": headers, "timeout": timeout})
        if self.overrides:
            response = self.overrides.popleft()
            if isinstance(response, Exception):
                raise response
            return response
        assert url == FILTER_URL, f"Unexpected Todoist request: {url}"
        start = int((params or {}).get("cursor", "0.0").split(".")[0])
        page = self.tasks[start : start + self.page_size]
        nxt = start + self.page_size
        cursor = f"{nxt}.x" if nxt < len(self.tasks) else None
        return FakeResponse({"results": page, "next_cursor": cursor})


@pytest.fixture
def manifest_data():
    return json.loads(MANIFEST_PATH.read_text())


@pytest.fixture
def todoist_api():
    fake = FakeTodoist()
    with patch("plugins.todoist.requests.get", side_effect=fake):
        yield fake


@pytest.fixture(autouse=True)
def fixed_now():
    with patch("plugins.todoist._now", side_effect=lambda tz=None: NOW if tz is None else NOW.replace(tzinfo=tz)):
        yield


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def clock():
    c = Clock()
    with patch("plugins.todoist._monotonic", side_effect=c):
        yield c


@pytest.fixture
def token():
    class Token:
        value = TEST_TOKEN

    return Token


@pytest.fixture
def make_plugin(manifest_data, token):
    def factory(**config):
        p = TodoistPlugin(manifest_data)
        p.config = {"enabled": True, **config}
        p.get_oauth_token = lambda: token.value
        return p

    return factory


@pytest.fixture
def plugin(make_plugin):
    return make_plugin()

"""Behaviour of the Todoist plugin: counts, next task, formatting, manifest."""

from plugins.todoist import TodoistPlugin, board_safe, priority_label

from .conftest import FILTER_URL, LAST_WEEK, TEST_TOKEN, TODAY, YESTERDAY, task


class TestRequest:
    def test_asks_for_today_or_overdue_with_bearer_token(self, plugin, todoist_api):
        result = plugin.fetch_data()
        assert result.available
        call = todoist_api.calls[0]
        assert call["url"] == FILTER_URL
        assert call["params"]["query"] == "today | overdue"
        assert call["params"]["limit"] == 200
        assert "cursor" not in call["params"]
        assert call["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"
        assert call["timeout"]

    def test_follows_next_cursor(self, plugin, todoist_api):
        todoist_api.page_size = 2
        result = plugin.fetch_data()
        assert len(todoist_api.calls) == 2
        assert todoist_api.calls[1]["params"]["cursor"] == "2.x"
        assert result.data["due_total"] == 4

    def test_stops_after_max_pages(self, plugin, todoist_api):
        todoist_api.tasks = [task(f"Task {i}", TODAY) for i in range(30)]
        todoist_api.page_size = 1
        result = plugin.fetch_data()
        assert len(todoist_api.calls) == 5
        assert result.data["due_today"] == 5


class TestCounts:
    def test_counts_today_and_overdue(self, plugin, todoist_api):
        data = plugin.fetch_data().data
        assert data["due_today"] == 3
        assert data["overdue"] == 1
        assert data["due_total"] == 4
        assert data["summary"] == "3 TODAY 1 OVERDUE"
        assert data["status_tile"] == "{63}"
        assert data["all_clear"] is False

    def test_only_today(self, plugin, todoist_api):
        todoist_api.tasks = [task("Pack gym bag", TODAY)]
        data = plugin.fetch_data().data
        assert data["summary"] == "1 TODAY"
        assert data["status_tile"] == "{65}"

    def test_only_overdue(self, plugin, todoist_api):
        todoist_api.tasks = [task("Call the plumber", LAST_WEEK), task("Fix the gate", YESTERDAY)]
        data = plugin.fetch_data().data
        assert data["summary"] == "2 OVERDUE"
        assert data["due_today"] == 0

    def test_all_clear(self, plugin, todoist_api):
        todoist_api.tasks = []
        data = plugin.fetch_data().data
        assert data["summary"] == "ALL CLEAR"
        assert data["status_tile"] == "{66}"
        assert data["all_clear"] is True
        assert data["next_task"] == ""
        assert data["next_task_due"] == ""
        assert data["next_task_priority"] == ""
        assert data["tasks"] == []

    def test_ignores_tasks_without_due_or_due_later(self, plugin, todoist_api):
        todoist_api.tasks = [task("No date", None), task("Future", "2026-10-09"), task("Today", TODAY)]
        data = plugin.fetch_data().data
        assert data["due_total"] == 1
        assert data["next_task"] == "TODAY"

    def test_utc_datetime_converted_to_local_zone(self, make_plugin, todoist_api):
        # 02:00Z on Oct 2 is still Oct 1 in Los Angeles: overdue there.
        todoist_api.tasks = [task("Late call", TODAY, time="02:00:00Z")]
        data = make_plugin(timezone="America/Los_Angeles").fetch_data().data
        assert data["overdue"] == 1
        assert data["next_task_due"] == "OVERDUE"

    def test_utc_datetime_without_zone_setting(self, plugin, todoist_api):
        todoist_api.tasks = [task("Late call", TODAY, time="12:00:00Z")]
        data = plugin.fetch_data().data
        assert data["due_total"] == 1

    def test_unparseable_due_is_skipped(self, plugin, todoist_api):
        bad = task("Odd", TODAY)
        bad["due"]["date"] = "not-a-date"
        todoist_api.tasks = [bad, task("Fine", TODAY)]
        assert plugin.fetch_data().data["due_total"] == 1


class TestNextTask:
    def test_oldest_overdue_first(self, plugin, todoist_api):
        todoist_api.tasks.append(task("Ancient chore", LAST_WEEK, priority=1))
        data = plugin.fetch_data().data
        assert data["next_task"] == "ANCIENT CHORE"
        assert data["next_task_due"] == "OVERDUE"
        assert data["next_task_priority"] == "P4"

    def test_timed_before_all_day_then_priority(self, plugin, todoist_api):
        todoist_api.tasks = todoist_api.tasks[1:]
        data = plugin.fetch_data().data
        assert data["next_task"] == "WATER THE FERNS"
        assert data["next_task_due"] == "8:30 AM"
        assert [t["content"] for t in data["tasks"]] == ["WATER THE FERNS", "RENEW LIBRARY BOOKS", "PACK GYM BAG"]
        assert data["tasks"][1] == {"content": "RENEW LIBRARY BOOKS", "due": "TODAY", "priority": "P2"}

    def test_afternoon_time(self, plugin, todoist_api):
        todoist_api.tasks = [task("Dentist", TODAY, time="15:05:00")]
        assert plugin.fetch_data().data["next_task_due"] == "3:05 PM"

    def test_bad_priority_sorts_as_normal(self, plugin, todoist_api):
        todoist_api.tasks = [task("Odd", TODAY, priority="x"), task("Urgent", TODAY, priority=4)]
        data = plugin.fetch_data().data
        assert data["next_task"] == "URGENT"
        assert data["tasks"][1]["priority"] == "P4"

    def test_task_list_capped(self, plugin, todoist_api):
        todoist_api.tasks = [task(f"Task {i}", TODAY, day_order=i) for i in range(12)]
        assert len(plugin.fetch_data().data["tasks"]) == 10

    def test_content_is_board_safe_and_fitted(self, plugin, todoist_api):
        todoist_api.tasks = [task("Café run 🚀 {with} a really long name here", TODAY)]
        assert plugin.fetch_data().data["next_task"] == "CAFE RUN WITH A REALLY"

    def test_markdown_links_reduced_to_text(self, plugin, todoist_api):
        todoist_api.tasks = [task("Read [the report](https://example.com/r) **now**", TODAY)]
        assert plugin.fetch_data().data["next_task"] == "READ THE REPORT NOW"


class TestFormattedLines:
    def test_default_layout(self, plugin, todoist_api):
        lines = plugin.fetch_data().formatted_lines
        assert len(lines) == 6
        assert lines[0] == "{63} TODOIST"
        assert lines[2] == "3 TODAY 1 OVERDUE"
        assert lines[4] == "NEXT UP"
        assert lines[5] == "CALL THE PLUMBER"

    def test_all_clear_layout(self, plugin, todoist_api):
        todoist_api.tasks = []
        lines = plugin.fetch_data().formatted_lines
        assert lines[2] == "ALL CLEAR"
        assert lines[4] == ""


class TestHelpers:
    def test_board_safe(self):
        assert board_safe(None) == ""
        assert board_safe("  héllo   wörld ") == "HELLO WORLD"

    def test_priority_label(self):
        assert priority_label(4) == "P1"
        assert priority_label(1) == "P4"
        assert priority_label("x") == "P4"
        assert priority_label(9) == "P4"


class TestManifest:
    def test_identity(self, manifest_data):
        assert manifest_data["id"] == "todoist"
        assert manifest_data["category"] == "utility"
        assert TodoistPlugin(manifest_data).plugin_id == "todoist"

    def test_oauth_block_is_public_client(self, manifest_data):
        oauth = manifest_data["oauth"]
        assert oauth["flows"] == ["relay"]
        assert oauth["authorization_url"] == "https://app.todoist.com/oauth/authorize"
        assert oauth["token_url"] == "https://api.todoist.com/oauth/access_token"
        assert oauth["scopes"] == ["data:read"]
        assert oauth["client_id"] == "https://fiestaboard.app/auth/clients/todoist.json"
        assert "client_secret" not in oauth
        assert "client_secret_setting" not in oauth
        props = manifest_data["settings_schema"]["properties"]
        assert "client_id" not in props and "client_secret" not in props

    def test_oauth_block_passes_core_validation(self, manifest_data):
        from src.oauth.provider import validate_provider_block

        assert validate_provider_block(manifest_data["oauth"]) == []

    def test_teaser_and_previews_fit(self, manifest_data):
        from src.text_to_board import take_tiles

        assert take_tiles(manifest_data["teaser"], 15)[0] == manifest_data["teaser"]
        sizes = {"flagship": (22, 6), "note": (15, 3)}
        for preview in manifest_data["previews"]:
            cols, rows = sizes[preview["device_type"]]
            assert len(preview["rows"]) <= rows
            for row in preview["rows"]:
                assert take_tiles(row, cols)[0] == row, row

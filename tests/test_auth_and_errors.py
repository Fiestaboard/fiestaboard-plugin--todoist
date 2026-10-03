"""Which credential is used, config validation, and every failure path."""

import requests

from plugins.todoist import (
    NOT_CONNECTED_ERROR,
    SIGN_IN_REJECTED_ERROR,
    TOKEN_REJECTED_ERROR,
    TodoistPlugin,
)

from .conftest import TEST_API_KEY, TEST_TOKEN, FakeResponse


def auth_header(api):
    return api.calls[-1]["headers"]["Authorization"]


class TestCredentials:
    def test_signed_in_token_wins_over_api_key(self, make_plugin, todoist_api):
        make_plugin(api_key=TEST_API_KEY).fetch_data()
        assert auth_header(todoist_api) == f"Bearer {TEST_TOKEN}"

    def test_api_key_used_when_not_signed_in(self, make_plugin, todoist_api, token):
        token.value = None
        result = make_plugin(api_key=f"  {TEST_API_KEY} ").fetch_data()
        assert result.available
        assert auth_header(todoist_api) == f"Bearer {TEST_API_KEY}"

    def test_api_key_used_on_platform_without_oauth(self, manifest_data, todoist_api, monkeypatch):
        p = TodoistPlugin(manifest_data)
        p.config = {"enabled": True, "api_key": TEST_API_KEY}
        monkeypatch.setattr(TodoistPlugin, "get_oauth_token", None, raising=False)
        assert p.fetch_data().available
        assert auth_header(todoist_api) == f"Bearer {TEST_API_KEY}"

    def test_oauth_lookup_failure_falls_back_to_api_key(self, make_plugin, todoist_api):
        p = make_plugin(api_key=TEST_API_KEY)

        def boom():
            raise RuntimeError("oauth service unavailable")

        p.get_oauth_token = boom
        assert p.fetch_data().available
        assert auth_header(todoist_api) == f"Bearer {TEST_API_KEY}"

    def test_nothing_configured(self, plugin, todoist_api, token):
        token.value = None
        result = plugin.fetch_data()
        assert not result.available
        assert result.error == NOT_CONNECTED_ERROR
        assert todoist_api.calls == []


class TestValidateConfig:
    def test_defaults_are_valid(self, plugin):
        assert plugin.validate_config({"enabled": True}) == []

    def test_bad_timezone(self, plugin):
        errors = plugin.validate_config({"timezone": "Mars/Olympus"})
        assert errors and "time zone" in errors[0].lower()

    def test_good_timezone(self, plugin):
        assert plugin.validate_config({"timezone": "Europe/Berlin"}) == []

    def test_refresh_below_minimum(self, plugin):
        assert plugin.validate_config({"refresh_seconds": 5})

    def test_bad_timezone_at_fetch_falls_back_to_local(self, make_plugin, todoist_api):
        assert make_plugin(timezone="Nowhere/Land").fetch_data().available

    def test_config_change_clears_cached_data(self, plugin, todoist_api, clock):
        plugin.fetch_data()
        plugin.on_config_change({}, {"enabled": True})
        todoist_api.fail(status=503)
        assert not plugin.fetch_data().available


class TestErrors:
    def test_401_with_sign_in(self, plugin, todoist_api):
        todoist_api.fail(status=401)
        result = plugin.fetch_data()
        assert not result.available
        assert result.error == SIGN_IN_REJECTED_ERROR

    def test_401_with_api_key(self, make_plugin, todoist_api, token):
        token.value = None
        todoist_api.fail(status=401)
        assert make_plugin(api_key=TEST_API_KEY).fetch_data().error == TOKEN_REJECTED_ERROR

    def test_403(self, plugin, todoist_api):
        todoist_api.fail(status=403)
        assert "403" in plugin.fetch_data().error

    def test_new_sign_in_skips_cooldown_of_old_credential(self, plugin, todoist_api, token):
        todoist_api.fail(status=403)
        assert "403" in plugin.fetch_data().error
        token.value = "test_reconnected_token"
        result = plugin.fetch_data()
        assert result.available
        assert auth_header(todoist_api) == "Bearer test_reconnected_token"

    def test_429_uses_retry_after_and_cools_down(self, plugin, todoist_api, clock):
        todoist_api.fail(status=429, headers={"Retry-After": "120"})
        result = plugin.fetch_data()
        assert "rate limit" in result.error.lower()
        calls = len(todoist_api.calls)
        clock.advance(60)
        assert not plugin.fetch_data().available
        assert len(todoist_api.calls) == calls
        clock.advance(61)
        assert plugin.fetch_data().available

    def test_429_bad_retry_after(self, plugin, todoist_api):
        todoist_api.fail(status=429, headers={"Retry-After": "soon"})
        assert "60s" in plugin.fetch_data().error

    def test_500(self, plugin, todoist_api):
        todoist_api.fail(status=502)
        assert "502" in plugin.fetch_data().error

    def test_unexpected_status(self, plugin, todoist_api):
        todoist_api.fail(status=418)
        assert "418" in plugin.fetch_data().error

    def test_timeout(self, plugin, todoist_api):
        todoist_api.fail(requests.exceptions.Timeout("slow"))
        assert "timed out" in plugin.fetch_data().error.lower()

    def test_connection_error(self, plugin, todoist_api):
        todoist_api.fail(requests.exceptions.ConnectionError("down"))
        assert "ConnectionError" in plugin.fetch_data().error

    def test_bad_json(self, plugin, todoist_api):
        todoist_api.fail(FakeResponse(ValueError("not json"), 200))
        assert "unexpected" in plugin.fetch_data().error.lower()

    def test_wrong_shape(self, plugin, todoist_api):
        todoist_api.fail(FakeResponse({"tasks": []}, 200))
        assert "unexpected" in plugin.fetch_data().error.lower()

    def test_never_raises(self, plugin, todoist_api, monkeypatch):
        monkeypatch.setattr(plugin, "_build_data", lambda *a: 1 / 0)
        result = plugin.fetch_data()
        assert not result.available
        assert "division" in result.error


class TestStaleData:
    def test_transient_error_shows_last_good_data(self, plugin, todoist_api, clock):
        assert plugin.fetch_data().available
        clock.advance(400)
        todoist_api.fail(status=503)
        result = plugin.fetch_data()
        assert result.available
        assert result.data["due_total"] == 4

    def test_stale_data_expires(self, plugin, todoist_api, clock):
        plugin.fetch_data()
        clock.advance(3 * 3600)
        todoist_api.fail(status=503)
        assert not plugin.fetch_data().available

    def test_auth_error_never_shows_stale_data(self, plugin, todoist_api, clock):
        plugin.fetch_data()
        todoist_api.fail(status=401)
        assert not plugin.fetch_data().available

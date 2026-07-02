"""Updater tests: version logic and release-check parsing (network mocked)."""

from __future__ import annotations

import io
import json
from unittest import mock

from editsync import updater


class TestVersionLogic:
    def test_parse_version(self):
        assert updater.parse_version("v1.2.3") == (1, 2, 3)
        assert updater.parse_version("1.10.0") == (1, 10, 0)
        assert updater.parse_version("v2.0.0-beta") == (2, 0, 0)
        assert updater.parse_version("garbage") == ()

    def test_is_newer(self):
        assert updater.is_newer("v1.1.0", current="1.0.1")
        assert updater.is_newer("v1.0.2", current="1.0.1")
        assert not updater.is_newer("v1.0.1", current="1.0.1")
        assert not updater.is_newer("v0.9.9", current="1.0.1")
        assert not updater.is_newer("not-a-version", current="1.0.1")


def _fake_release(tag: str, assets: list[str]) -> mock.MagicMock:
    body = json.dumps(
        {
            "tag_name": tag,
            "html_url": f"https://github.com/x/y/releases/tag/{tag}",
            "assets": [
                {"name": name, "url": f"https://api.github.com/assets/{name}"}
                for name in assets
            ],
        }
    ).encode()
    response = mock.MagicMock()
    response.read.return_value = body
    response.__enter__ = lambda s: s
    response.__exit__ = mock.MagicMock(return_value=False)
    return response


class TestCheckForUpdate:
    def test_newer_release_found(self, monkeypatch):
        monkeypatch.setattr(updater, "__version__", "1.0.0", raising=False)
        fake = _fake_release("v9.9.9", ["EditSync.dmg", "EditSync-windows.zip"])
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=fake), \
             mock.patch.object(json, "load", side_effect=lambda r: json.loads(r.read())):
            info = updater.check_for_update()
        assert info is not None
        assert info.version == "9.9.9"
        assert updater.platform_asset_name() in info.asset_url

    def test_same_version_returns_none(self):
        fake = _fake_release(f"v{updater.__version__}", ["EditSync.dmg"])
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=fake), \
             mock.patch.object(json, "load", side_effect=lambda r: json.loads(r.read())):
            assert updater.check_for_update() is None

    def test_missing_platform_asset_returns_none(self):
        fake = _fake_release("v9.9.9", ["SomethingElse.zip"])
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=fake), \
             mock.patch.object(json, "load", side_effect=lambda r: json.loads(r.read())):
            assert updater.check_for_update() is None

    def test_network_error_returns_none(self):
        with mock.patch.object(
            updater.urllib.request,
            "urlopen",
            side_effect=updater.urllib.error.URLError("offline"),
        ):
            assert updater.check_for_update() is None

    def test_token_header_used_when_present(self, monkeypatch):
        monkeypatch.setenv("EDITSYNC_GITHUB_TOKEN", "tok123")
        req = updater._request("https://example.com", "application/json")
        assert req.get_header("Authorization") == "Bearer tok123"

    def test_not_frozen_has_no_app_root(self):
        assert updater.current_app_root() is None

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


class TestDetailedCheck:
    """The visible check must say what happened, never just go quiet."""

    def test_update_available(self, monkeypatch):
        monkeypatch.setattr(updater, "__version__", "1.0.0", raising=False)
        fake = _fake_release("v9.9.9", ["EditSync.dmg", "EditSync-windows.zip"])
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=fake), \
             mock.patch.object(json, "load", side_effect=lambda r: json.loads(r.read())):
            result = updater.check_for_update_detailed()
        assert result.status == "update"
        assert result.info is not None and result.info.version == "9.9.9"

    def test_up_to_date_reports_current(self):
        fake = _fake_release(f"v{updater.__version__}", ["EditSync.dmg"])
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=fake), \
             mock.patch.object(json, "load", side_effect=lambda r: json.loads(r.read())):
            result = updater.check_for_update_detailed()
        assert result.status == "current"
        assert result.detail == updater.__version__

    def test_missing_asset_is_an_actionable_error(self, monkeypatch):
        monkeypatch.setattr(updater, "__version__", "1.0.0", raising=False)
        fake = _fake_release("v9.9.9", ["SomethingElse.zip"])
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=fake), \
             mock.patch.object(json, "load", side_effect=lambda r: json.loads(r.read())):
            result = updater.check_for_update_detailed()
        assert result.status == "error"
        assert "isn't ready yet" in result.detail

    def test_offline_is_a_plain_language_error(self):
        with mock.patch.object(
            updater.urllib.request,
            "urlopen",
            side_effect=updater.urllib.error.URLError("no route to host"),
        ):
            result = updater.check_for_update_detailed()
        assert result.status == "error"
        assert "internet connection" in result.detail

    def test_certificate_failure_is_called_out(self):
        import ssl

        with mock.patch.object(
            updater.urllib.request,
            "urlopen",
            side_effect=updater.urllib.error.URLError(
                ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")
            ),
        ):
            result = updater.check_for_update_detailed()
        assert result.status == "error"
        assert "certificate" in result.detail.lower()

    def test_rate_limit_is_explained(self):
        err = updater.urllib.error.HTTPError(
            updater.API_LATEST, 403, "rate limited", {}, io.BytesIO(b"")
        )
        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=err
        ):
            result = updater.check_for_update_detailed()
        assert result.status == "error"
        assert "rate-limiting" in result.detail

    def test_wrapper_delegates_to_detailed(self, monkeypatch):
        sentinel = updater.UpdateInfo("9.9.9", "v9.9.9", "u", "p")
        monkeypatch.setattr(
            updater,
            "check_for_update_detailed",
            lambda: updater.CheckResult("update", info=sentinel),
        )
        assert updater.check_for_update() is sentinel


class TestSSLContext:
    def test_context_combines_platform_and_certifi_stores(self):
        import certifi
        import ssl

        context = updater._ssl_context()
        assert isinstance(context, ssl.SSLContext)
        assert certifi.where()  # the packaged app has no other CAs
        # certifi's CAs really are loaded (frozen macOS has zero without it)
        assert context.cert_store_stats()["x509_ca"] > 0

    def test_truncated_response_never_raises(self):
        """IncompleteRead subclasses neither OSError nor ValueError —
        it must still become a CheckResult, or the footer wedges at
        'Checking…' forever (confirmed by the adversarial review)."""
        import http.client

        with mock.patch.object(
            updater.urllib.request,
            "urlopen",
            side_effect=http.client.IncompleteRead(b"partial"),
        ):
            result = updater.check_for_update_detailed()
        assert result.status == "error"
        assert "interrupted" in result.detail

    def test_download_can_be_cancelled(self, tmp_path):
        info = updater.UpdateInfo("9.9.9", "v9.9.9", "http://x/asset", "p")
        response = mock.MagicMock()
        response.headers = {"Content-Length": "1000000"}
        response.read.return_value = b"x" * 1024
        response.__enter__ = lambda s: s
        response.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=response
        ):
            try:
                updater.download_asset(info, cancelled=lambda: True)
            except updater.UpdateError as exc:
                assert "cancelled" in str(exc).lower()
            else:
                raise AssertionError("cancellation must raise UpdateError")

    def test_requests_pass_the_context(self):
        """Both network calls must send our verified SSL context —
        a bare urlopen has no CA store inside the packaged app."""
        captured = {}

        def fake_urlopen(request, timeout=None, context=None):
            captured["context"] = context
            raise updater.urllib.error.URLError("stop here")

        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            updater.check_for_update_detailed()
        import ssl

        assert isinstance(captured["context"], ssl.SSLContext)

"""Custom background chain: the picker filter, save_config validation and
get_background's mime map must agree on the extension set."""

import base64
from unittest.mock import patch

import config_store
import js_api
from js_api import Api

ACCEPTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _make_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def test_picker_filter_matches_validation():
    """The PowerShell filter offers exactly the extensions save_config
    accepts (regression: .gif was once missing from one side)."""
    exts = {e.strip().lstrip("*").lower()
            for e in js_api._BG_FILTER.split(";")}
    assert exts == ACCEPTED


def test_get_background_serves_gif(tmp_path, monkeypatch):
    """A .gif accepted by validation must actually render (the mime map used
    to drop it, so the picked background silently never showed)."""
    api = _make_api(tmp_path, monkeypatch)
    gif = tmp_path / "bg.gif"
    gif.write_bytes(b"GIF89a" + b"\x00" * 8)
    assert api.save_config({"bg_path": str(gif)}) is True
    r = api.get_background()
    assert r and r["data"].startswith("data:image/gif;base64,")
    assert base64.b64decode(r["data"].split(",", 1)[1]) == gif.read_bytes()

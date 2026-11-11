"""Config backup / one-click restore.

The load-bearing contract: restore routes through save_config, which persists
the ownership records (restore/applied/wsl_written) from IN-MEMORY state and
never from caller data. So restoring an old backup can bring back form fields
and preferences, but can NEVER resurrect a stale applied/restore record and
make quit-clear claim the user's current proxy. Backups live in a managed
folder; names are validated so a caller cannot read/delete arbitrary paths.
"""

import json
import os
from unittest.mock import patch

import config_store
from js_api import Api


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.scheme, api.user, api.password = "http", "", ""
    return api


def _backup_path(tmp_path, name):
    return tmp_path / "backups" / name


def test_backup_encrypts_password_on_disk(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.password = "s3cret"
    r = api.backup_config()
    assert r["ok"] is True
    raw = json.loads(_backup_path(tmp_path, r["name"]).read_text(encoding="utf-8"))
    assert str(raw["password"]).startswith("DPAPI:")
    assert "s3cret" not in json.dumps(raw)


def test_backup_flushes_current_form_first(tmp_path, monkeypatch):
    """backup_config must snapshot the live in-memory form, not a stale file:
    it calls save_config({}) before copying."""
    api = _api(tmp_path, monkeypatch)
    api.save_config({})                      # seed config.json at 7897
    api.host, api.port = "10.0.0.9", "1080"  # typed, not yet persisted
    r = api.backup_config()
    raw = json.loads(_backup_path(tmp_path, r["name"]).read_text(encoding="utf-8"))
    assert raw["host"] == "10.0.0.9" and raw["port"] == "1080"


def test_list_backups_newest_first(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    names = [api.backup_config()["name"] for _ in range(3)]
    listed = [b["name"] for b in api.list_backups()]
    assert set(listed) == set(names)
    mtimes = [b["mtime"] for b in api.list_backups()]
    assert mtimes == sorted(mtimes, reverse=True)


def test_restore_restores_form_not_ownership(tmp_path, monkeypatch):
    """THE invariant: restoring an old backup brings back the form address but
    must NOT roll applied_* back — the endpoints currently managed stay owned
    by the live in-memory record, never by the backup's stale one."""
    api = _api(tmp_path, monkeypatch)
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    name = api.backup_config()["name"]        # snapshot form+applied = 7897

    # The tool later switches to a different address; ownership moves with it.
    api.host = api.applied_host = "192.168.1.50"
    api.port = api.applied_port = "8080"
    api.save_config({})

    assert api.restore_backup(name)["ok"] is True
    # Form restored from the backup...
    assert api.host == "127.0.0.1" and api.port == "7897"
    # ...but ownership is still the live record, NOT the backup's 7897.
    assert api.applied_host == "192.168.1.50"
    assert api.applied_port == "8080"
    disk = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert disk["applied"]["host"] == "192.168.1.50"
    assert disk["applied"]["port"] == "8080"


def test_restore_password_roundtrip(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.user, api.password = "alice", "s3cret"
    name = api.backup_config()["name"]
    api.user, api.password = "bob", "other"
    api.save_config({})
    assert api.restore_backup(name)["ok"] is True
    assert api.user == "alice" and api.password == "s3cret"
    # Re-persisted password is encrypted again, never the DPAPI blob doubled up.
    disk = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert str(disk["password"]).startswith("DPAPI:")
    assert config_store.load_config()["password"] == "s3cret"


def test_restore_never_touches_restore_stash(tmp_path, monkeypatch):
    """The restore stash holds the user's original proxy URLs. A backup taken
    before a stash existed must not wipe a later stash on restore."""
    api = _api(tmp_path, monkeypatch)
    name = api.backup_config()["name"]         # no stash yet
    api._restore = {"npm": "http://10.0.0.1:1"}
    api.save_config({})
    assert api.restore_backup(name)["ok"] is True
    assert api._restore == {"npm": "http://10.0.0.1:1"}
    # The stash survived on disk (DPAPI-encrypted, not wiped to {}).
    disk = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert str(disk["restore"]["npm"]).startswith("DPAPI:")
    assert config_store._dpapi_decrypt(disk["restore"]["npm"]) == "http://10.0.0.1:1"


def test_restore_missing_backup(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    r = api.restore_backup("config-19990101-000000.json")
    assert r["ok"] is False and r["error_key"] == "backupMissing"


def test_restore_rejects_traversal_name(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    # A name that would escape the backups folder must be rejected, not read.
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"host": "evil.example", "port": "1"}),
                      encoding="utf-8")
    for bad in ("../secret.json", "..\\secret.json", "secret.json",
                "../../etc/passwd", "config-20200101-000000.json/../../x"):
        r = api.restore_backup(bad)
        assert r["ok"] is False and r["error_key"] == "backupMissing"
    assert api.host == "127.0.0.1"            # unchanged


def test_delete_backup(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    name = api.backup_config()["name"]
    assert api.delete_backup(name)["ok"] is True
    assert api.list_backups() == []
    assert not _backup_path(tmp_path, name).exists()


def test_delete_rejects_traversal_name(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    keep = tmp_path / "keep.json"
    keep.write_text("{}", encoding="utf-8")
    for bad in ("../keep.json", "..\\keep.json", "keep.json"):
        assert api.delete_backup(bad)["ok"] is False
    assert keep.exists()


def test_max_backups_pruning(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    for _ in range(config_store.MAX_BACKUPS + 5):
        assert api.backup_config()["ok"] is True
    assert len(api.list_backups()) == config_store.MAX_BACKUPS


def test_backup_name_matches_pattern(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    name = api.backup_config()["name"]
    assert config_store._BACKUP_NAME_RE.match(name)
    # No temp leftovers in the backups folder.
    leftovers = [n for n in os.listdir(tmp_path / "backups") if n.endswith(".tmp")]
    assert leftovers == []
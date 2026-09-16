"""Canonical proxy URL builder / parser — HTTP, SOCKS5, and userinfo."""

import proxy_manager as pm


def test_build_http_plain():
    assert pm.build_proxy_url("127.0.0.1", "7897") == "http://127.0.0.1:7897"


def test_build_socks5_with_auth():
    url = pm.build_proxy_url("127.0.0.1", 7891, "socks5", "alice", "p@ss:word")
    assert url.startswith("socks5://alice:")
    assert url.endswith("@127.0.0.1:7891")
    parsed = pm.parse_proxy_url(url)
    assert parsed["user"] == "alice"
    assert parsed["password"] == "p@ss:word"
    assert parsed["scheme"] == "socks5"
    assert parsed["host"] == "127.0.0.1"
    assert parsed["port"] == "7891"


def test_parse_rejects_garbage():
    assert pm.parse_proxy_url("") is None
    assert pm.parse_proxy_url("not-a-url") is None
    assert pm.parse_proxy_url("ftp://127.0.0.1:80") is None
    assert pm.parse_proxy_url(None) is None


def test_parse_https_normalizes_to_http():
    parsed = pm.parse_proxy_url("https://proxy.local:8080")
    assert parsed["scheme"] == "http"
    assert parsed["host"] == "proxy.local"
    assert parsed["port"] == "8080"


def test_is_proxy_url():
    assert pm.is_proxy_url("http://127.0.0.1:7897")
    assert pm.is_proxy_url("socks5h://127.0.0.1:1080")
    assert not pm.is_proxy_url("未设置")


def test_is_valid_scheme_and_userinfo():
    assert pm.is_valid_scheme("http")
    assert pm.is_valid_scheme("SOCKS5")
    assert pm.is_valid_scheme("socks5h")
    assert not pm.is_valid_scheme("ftp")
    assert pm.is_valid_userinfo("")
    assert pm.is_valid_userinfo("alice")
    assert pm.is_valid_userinfo("a_b.1-2")
    assert not pm.is_valid_userinfo("a:b")
    assert not pm.is_valid_userinfo("a b")
    assert pm.is_valid_userinfo("a@b")
    assert pm.is_valid_userinfo("a!b")


def test_parse_preserves_host_case():
    parsed = pm.parse_proxy_url("http://My.Proxy.Local:7897")
    assert parsed["host"] == "My.Proxy.Local"
    rebuilt = pm.build_proxy_url(parsed["host"], parsed["port"], parsed["scheme"])
    assert rebuilt == "http://My.Proxy.Local:7897"


def test_wininet_url_drops_auth_and_normalizes_socks():
    assert pm.wininet_url("127.0.0.1", "7897", "http") == "http://127.0.0.1:7897"
    assert pm.wininet_url("127.0.0.1", "7891", "socks5h") == "socks5://127.0.0.1:7891"
    assert pm.wininet_url("127.0.0.1", "7891", "socks5") == "socks5://127.0.0.1:7891"

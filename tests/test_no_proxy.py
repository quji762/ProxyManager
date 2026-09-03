"""NO_PROXY merge/strip must preserve user entries and only manage ours."""

from proxy_manager import NO_PROXY_VALUE, _merged_no_proxy, _stripped_no_proxy


def test_merge_adds_loopback_without_clobber():
    merged = _merged_no_proxy("example.com,.corp")
    assert merged.startswith("example.com,.corp")
    for e in NO_PROXY_VALUE.split(","):
        assert e in merged.split(",")


def test_merge_does_not_duplicate():
    merged = _merged_no_proxy("localhost,127.0.0.1")
    assert merged.split(",").count("localhost") == 1


def test_strip_keeps_user_entries():
    existing = "example.com,localhost,127.0.0.1,::1,.corp"
    kept = _stripped_no_proxy(existing)
    assert "example.com" in kept
    assert ".corp" in kept
    assert "localhost" not in kept.split(",")
    assert "127.0.0.1" not in kept.split(",")


def test_sys_proxy_server_to_url_http_and_socks():
    from proxy_manager import _sys_proxy_server_to_url
    assert _sys_proxy_server_to_url("127.0.0.1:7897") == "http://127.0.0.1:7897"
    assert _sys_proxy_server_to_url("socks=127.0.0.1:7891") == "socks5://127.0.0.1:7891"
    assert _sys_proxy_server_to_url("http=127.0.0.1:7890;https=127.0.0.1:7890") == \
        "http://127.0.0.1:7890"
    assert _sys_proxy_server_to_url("") is None

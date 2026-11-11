"""WSL managed-block export parser (POSIX and fish)."""

from proxy_manager import _parse_export

BLOCK = '''# >>> proxy-manager begin >>>
export HTTP_PROXY="http://172.24.0.1:7897"
export HTTPS_PROXY="http://172.24.0.1:7897"
export ALL_PROXY="http://172.24.0.1:7897"
# <<< proxy-manager end <<<
'''

FISH = '''set -gx HTTP_PROXY "socks5://172.24.0.1:7891"
set -gx ALL_PROXY "socks5://172.24.0.1:7891"
'''


def test_parse_posix_export():
    assert _parse_export(BLOCK, "HTTP_PROXY") == "http://172.24.0.1:7897"
    assert _parse_export(BLOCK, "ALL_PROXY") == "http://172.24.0.1:7897"
    assert _parse_export(BLOCK, "NO_PROXY") is None


def test_parse_fish():
    assert _parse_export(FISH, "HTTP_PROXY") == "socks5://172.24.0.1:7891"
    assert _parse_export(FISH, "ALL_PROXY") == "socks5://172.24.0.1:7891"


def test_format_speed():
    from proxy_manager import format_speed
    assert format_speed(100).endswith("B/s")
    assert "KB" in format_speed(1500)
    assert "MB" in format_speed(2_500_000)


def test_format_speed_boundary_promotes():
    """Regression: 999,999 B/s must display as '1.0 MB/s', not '1000.0 KB/s'."""
    from proxy_manager import format_speed
    assert format_speed(999_999) == "1.0 MB/s"
    assert format_speed(999_999_999) == "1.0 GB/s"
    # Exact boundaries must not double-promote.
    assert format_speed(1_000_000) == "1.0 MB/s"
    assert format_speed(1_000_000_000) == "1.0 GB/s"

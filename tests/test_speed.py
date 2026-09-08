"""Speed display: a value that ROUNDS to "1000" must promote to the next
unit (999.6 B/s used to display as "1000 B/s")."""

from proxy_manager import format_speed


def test_999_6_promotes_to_kb():
    assert format_speed(999.6) == "1.0 KB/s"


def test_949_9_stays_bytes():
    assert format_speed(949.9) == "950 B/s"


def test_999_4_stays_bytes():
    assert format_speed(999.4) == "999 B/s"


def test_higher_tiers_promote_at_display_boundary():
    assert format_speed(1000) == "1.0 KB/s"
    # 999,960 B/s → 999.96 KB → would display "1000.0 KB" → must be 1.0 MB.
    assert format_speed(999960) == "1.0 MB/s"


def test_gb_boundary_promotes_to_tb():
    # 999,999,600,000 B/s ≈ 999.9996 GB → used to display "1000.0 GB/s";
    # with a TB unit it must promote instead.
    assert format_speed(999999600000) == "1.0 TB/s"


def test_gb_below_boundary_stays_gb():
    assert format_speed(999_000_000_000) == "999.0 GB/s"


def test_tb_top_clamps_display():
    # Beyond TB there is no further unit; the value just displays large.
    assert format_speed(2_000_000_000_000) == "2.0 TB/s"

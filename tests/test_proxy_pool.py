"""utils/proxy_pool.py 单元测试。"""
from __future__ import annotations

from utils import proxy_pool as pp


# ----------------------------- TestIsPort -----------------------------
class TestIsPort:
    def test_valid_port(self) -> None:
        assert pp._is_port("1") is True
        assert pp._is_port("80") is True
        assert pp._is_port("65535") is True

    def test_zero_invalid(self) -> None:
        assert pp._is_port("0") is False

    def test_over_range_invalid(self) -> None:
        assert pp._is_port("65536") is False
        assert pp._is_port("99999") is False

    def test_non_digit_invalid(self) -> None:
        assert pp._is_port("") is False
        assert pp._is_port("8a0") is False
        assert pp._is_port("-1") is False


# ----------------------------- TestParseProxyLine -----------------------------
class TestParseProxyLine:
    def test_none_returns_none(self) -> None:
        assert pp.parse_proxy_line(None) is None

    def test_empty_returns_none(self) -> None:
        assert pp.parse_proxy_line("") is None
        assert pp.parse_proxy_line("   ") is None

    def test_comment_returns_none(self) -> None:
        assert pp.parse_proxy_line("# this is a comment") is None
        assert pp.parse_proxy_line("   # indented comment") is None

    def test_host_port_defaults_http(self) -> None:
        assert pp.parse_proxy_line("1.2.3.4:8080") == "http://1.2.3.4:8080"

    def test_host_port_user_pass(self) -> None:
        assert pp.parse_proxy_line("1.2.3.4:8080:user:pass") == "http://user:pass@1.2.3.4:8080"

    def test_scheme_host_port_passthrough(self) -> None:
        assert pp.parse_proxy_line("socks5://1.2.3.4:1080") == "socks5://1.2.3.4:1080"
        assert pp.parse_proxy_line("https://proxy.example.com:443") == "https://proxy.example.com:443"

    def test_scheme_user_pass_at_host_port(self) -> None:
        assert pp.parse_proxy_line("http://user:pass@1.2.3.4:8080") == "http://user:pass@1.2.3.4:8080"

    def test_scheme_host_port_user_pass_normalized(self) -> None:
        assert pp.parse_proxy_line("socks5://1.2.3.4:1080:user:pass") == "socks5://user:pass@1.2.3.4:1080"

    def test_scheme_case_insensitive(self) -> None:
        assert pp.parse_proxy_line("SOCKS5://1.2.3.4:1080") == "socks5://1.2.3.4:1080"
        assert pp.parse_proxy_line("HtTp://1.2.3.4:8080") == "http://1.2.3.4:8080"

    def test_whitespace_trimmed(self) -> None:
        assert pp.parse_proxy_line("  1.2.3.4:8080  ") == "http://1.2.3.4:8080"

    def test_hostname_not_ip(self) -> None:
        assert pp.parse_proxy_line("proxy.example.com:3128") == "http://proxy.example.com:3128"

    def test_all_allowed_schemes(self) -> None:
        for scheme in ("http", "https", "socks4", "socks5", "socks5h"):
            assert pp.parse_proxy_line(f"{scheme}://1.2.3.4:1080") == f"{scheme}://1.2.3.4:1080"

    # ---- 非法输入 ----
    def test_unknown_scheme_invalid(self) -> None:
        assert pp.parse_proxy_line("htp://1.2.3.4:8080") is None
        assert pp.parse_proxy_line("ftp://1.2.3.4:21") is None

    def test_missing_port_invalid(self) -> None:
        assert pp.parse_proxy_line("1.2.3.4") is None

    def test_bad_port_invalid(self) -> None:
        assert pp.parse_proxy_line("1.2.3.4:0") is None
        assert pp.parse_proxy_line("1.2.3.4:70000") is None
        assert pp.parse_proxy_line("1.2.3.4:abc") is None

    def test_empty_host_invalid(self) -> None:
        assert pp.parse_proxy_line(":8080") is None

    def test_three_parts_invalid(self) -> None:
        assert pp.parse_proxy_line("1.2.3.4:8080:user") is None

    def test_empty_auth_segment_invalid(self) -> None:
        assert pp.parse_proxy_line("1.2.3.4:8080::pass") is None
        assert pp.parse_proxy_line("1.2.3.4:8080:user:") is None

    def test_scheme_only_invalid(self) -> None:
        assert pp.parse_proxy_line("http://") is None

    def test_at_without_auth_invalid(self) -> None:
        assert pp.parse_proxy_line("http://@1.2.3.4:8080") is None

    def test_at_without_hostport_invalid(self) -> None:
        assert pp.parse_proxy_line("http://user:pass@") is None

    def test_at_host_with_embedded_auth_invalid(self) -> None:
        # @ 后面再带 user:pass 形式（4 段）应判非法
        assert pp.parse_proxy_line("http://user:pass@1.2.3.4:8080:x:y") is None

    # ---- IPv6 ----
    def test_ipv6_host_port(self) -> None:
        assert pp.parse_proxy_line("[2001:db8::1]:1080") == "http://[2001:db8::1]:1080"

    def test_ipv6_with_scheme(self) -> None:
        assert pp.parse_proxy_line("socks5://[2001:db8::1]:1080") == "socks5://[2001:db8::1]:1080"

    def test_ipv6_host_port_user_pass(self) -> None:
        assert pp.parse_proxy_line("[2001:db8::1]:1080:user:pass") == "http://user:pass@[2001:db8::1]:1080"

    def test_ipv6_unclosed_bracket_invalid(self) -> None:
        assert pp.parse_proxy_line("[2001:db8::1:1080") is None

    def test_ipv6_missing_port_invalid(self) -> None:
        assert pp.parse_proxy_line("[2001:db8::1]") is None


# ----------------------------- TestParseProxyPool -----------------------------
class TestParseProxyPool:
    def test_none_returns_empty(self) -> None:
        assert pp.parse_proxy_pool(None) == []

    def test_empty_string_returns_empty(self) -> None:
        assert pp.parse_proxy_pool("") == []

    def test_empty_list_returns_empty(self) -> None:
        assert pp.parse_proxy_pool([]) == []

    def test_multiline_string(self) -> None:
        text = "1.2.3.4:8080\nsocks5://5.6.7.8:1080"
        assert pp.parse_proxy_pool(text) == [
            "http://1.2.3.4:8080",
            "socks5://5.6.7.8:1080",
        ]

    def test_list_input(self) -> None:
        assert pp.parse_proxy_pool(["1.2.3.4:8080", "5.6.7.8:3128"]) == [
            "http://1.2.3.4:8080",
            "http://5.6.7.8:3128",
        ]

    def test_tuple_input(self) -> None:
        assert pp.parse_proxy_pool(("1.2.3.4:8080",)) == ["http://1.2.3.4:8080"]

    def test_skips_blank_and_comment_lines(self) -> None:
        text = "\n# 注释\n1.2.3.4:8080\n\n  \n# another\n5.6.7.8:3128\n"
        assert pp.parse_proxy_pool(text) == [
            "http://1.2.3.4:8080",
            "http://5.6.7.8:3128",
        ]

    def test_skips_invalid_lines(self) -> None:
        text = "1.2.3.4:8080\ngarbage\nftp://x:21\n5.6.7.8:3128"
        assert pp.parse_proxy_pool(text) == [
            "http://1.2.3.4:8080",
            "http://5.6.7.8:3128",
        ]

    def test_dedup_preserves_order(self) -> None:
        text = "1.2.3.4:8080\n5.6.7.8:3128\n1.2.3.4:8080"
        assert pp.parse_proxy_pool(text) == [
            "http://1.2.3.4:8080",
            "http://5.6.7.8:3128",
        ]

    def test_dedup_after_normalization(self) -> None:
        # 两种写法规范化后相同，应去重
        text = "1.2.3.4:8080\nhttp://1.2.3.4:8080"
        assert pp.parse_proxy_pool(text) == ["http://1.2.3.4:8080"]

    def test_list_items_may_contain_newlines(self) -> None:
        assert pp.parse_proxy_pool(["1.2.3.4:8080\n5.6.7.8:3128"]) == [
            "http://1.2.3.4:8080",
            "http://5.6.7.8:3128",
        ]

    def test_crlf_line_endings(self) -> None:
        text = "1.2.3.4:8080\r\n5.6.7.8:3128"
        assert pp.parse_proxy_pool(text) == [
            "http://1.2.3.4:8080",
            "http://5.6.7.8:3128",
        ]

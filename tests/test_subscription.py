"""subscription 单元测试。

覆盖订阅转 raw_proxy_pool 工具的三种输入格式解析与跳过统计：
- Clash YAML（proxies 段，跳过 vmess/ss 等非 http/socks 类型）
- base64 订阅（解码成 URI 列表）
- 纯文本 / URI 列表（跳过非 http/socks scheme）

不触网（fetch_subscription 不在测试范围内）。
"""

from __future__ import annotations

import base64

import pytest

from utils import subscription
from utils.config import has_yaml


# --------------------------------------------------------------------------- #
# convert_subscription：空输入
# --------------------------------------------------------------------------- #
class TestEmptyInput:
    def test_none(self) -> None:
        proxies, skipped = subscription.convert_subscription(None)
        assert proxies == []
        assert skipped == {}

    def test_empty_string(self) -> None:
        proxies, skipped = subscription.convert_subscription("   ")
        assert proxies == []
        assert skipped == {}


# --------------------------------------------------------------------------- #
# convert_subscription：纯文本 / URI 列表
# --------------------------------------------------------------------------- #
class TestUriList:
    def test_plain_host_port(self) -> None:
        text = "1.2.3.4:8080\n5.6.7.8:1080:user:pass"
        proxies, skipped = subscription.convert_subscription(text)
        assert proxies == [
            "http://1.2.3.4:8080",
            "http://user:pass@5.6.7.8:1080",
        ]
        assert skipped == {}

    def test_scheme_uris(self) -> None:
        text = "socks5://9.9.9.9:1080\nhttp://1.1.1.1:3128"
        proxies, _ = subscription.convert_subscription(text)
        assert proxies == ["socks5://9.9.9.9:1080", "http://1.1.1.1:3128"]

    def test_skip_comments_and_blanks(self) -> None:
        text = "# 注释\n\n1.2.3.4:8080\n  \n"
        proxies, skipped = subscription.convert_subscription(text)
        assert proxies == ["http://1.2.3.4:8080"]
        assert skipped == {}

    def test_skip_unsupported_scheme(self) -> None:
        text = (
            "vmess://abcdef\n"
            "ss://xyz\n"
            "trojan://qwe\n"
            "http://1.1.1.1:3128\n"
        )
        proxies, skipped = subscription.convert_subscription(text)
        assert proxies == ["http://1.1.1.1:3128"]
        assert skipped == {"vmess": 1, "ss": 1, "trojan": 1}

    def test_dedup(self) -> None:
        text = "1.2.3.4:8080\nhttp://1.2.3.4:8080"
        proxies, _ = subscription.convert_subscription(text)
        assert proxies == ["http://1.2.3.4:8080"]


# --------------------------------------------------------------------------- #
# convert_subscription：base64 订阅
# --------------------------------------------------------------------------- #
class TestBase64:
    def test_base64_uri_list(self) -> None:
        inner = "socks5://9.9.9.9:1080\nhttp://1.1.1.1:3128"
        encoded = base64.b64encode(inner.encode("utf-8")).decode("ascii")
        proxies, skipped = subscription.convert_subscription(encoded)
        assert proxies == ["socks5://9.9.9.9:1080", "http://1.1.1.1:3128"]
        assert skipped == {}

    def test_base64_without_padding(self) -> None:
        inner = "http://1.1.1.1:3128"
        encoded = base64.b64encode(inner.encode("utf-8")).decode("ascii").rstrip("=")
        proxies, _ = subscription.convert_subscription(encoded)
        assert proxies == ["http://1.1.1.1:3128"]

    def test_base64_skips_unsupported(self) -> None:
        inner = "vmess://aaa\nhttp://1.1.1.1:3128"
        encoded = base64.b64encode(inner.encode("utf-8")).decode("ascii")
        proxies, skipped = subscription.convert_subscription(encoded)
        assert proxies == ["http://1.1.1.1:3128"]
        assert skipped == {"vmess": 1}

    def test_non_base64_falls_through_to_text(self) -> None:
        # 不是 base64 的普通文本应走 URI 列表分支
        proxies, _ = subscription.convert_subscription("1.2.3.4:8080")
        assert proxies == ["http://1.2.3.4:8080"]


# --------------------------------------------------------------------------- #
# convert_subscription：Clash YAML
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not has_yaml(), reason="PyYAML 未安装")
class TestClashYaml:
    def test_http_and_socks_nodes(self) -> None:
        text = (
            "proxies:\n"
            "  - name: a\n"
            "    type: http\n"
            "    server: 1.1.1.1\n"
            "    port: 3128\n"
            "  - name: b\n"
            "    type: socks5\n"
            "    server: 2.2.2.2\n"
            "    port: 1080\n"
        )
        proxies, skipped = subscription.convert_subscription(text)
        assert proxies == ["http://1.1.1.1:3128", "socks5://2.2.2.2:1080"]
        assert skipped == {}

    def test_node_with_auth(self) -> None:
        text = (
            "proxies:\n"
            "  - name: a\n"
            "    type: http\n"
            "    server: 1.1.1.1\n"
            "    port: 3128\n"
            "    username: u\n"
            "    password: p\n"
        )
        proxies, _ = subscription.convert_subscription(text)
        assert proxies == ["http://u:p@1.1.1.1:3128"]

    def test_skip_unsupported_types(self) -> None:
        text = (
            "proxies:\n"
            "  - name: a\n"
            "    type: vmess\n"
            "    server: 1.1.1.1\n"
            "    port: 443\n"
            "  - name: b\n"
            "    type: ss\n"
            "    server: 2.2.2.2\n"
            "    port: 8388\n"
            "  - name: c\n"
            "    type: http\n"
            "    server: 3.3.3.3\n"
            "    port: 3128\n"
        )
        proxies, skipped = subscription.convert_subscription(text)
        assert proxies == ["http://3.3.3.3:3128"]
        assert skipped == {"vmess": 1, "ss": 1}

    def test_missing_server_or_port_skipped(self) -> None:
        text = (
            "proxies:\n"
            "  - name: a\n"
            "    type: http\n"
            "    server: 1.1.1.1\n"
            "  - name: b\n"
            "    type: http\n"
            "    server: 2.2.2.2\n"
            "    port: 3128\n"
        )
        proxies, _ = subscription.convert_subscription(text)
        assert proxies == ["http://2.2.2.2:3128"]

"""mail_provider 单元测试。

只测试不依赖网络的纯函数与工厂逻辑：
- _random_mailbox_name / _random_subdomain_label（随机字符生成）
- _next_domain（域名轮询）
- _extract_code（验证码提取正则）
- _FORBIDDEN_CODES（OpenAI 哨兵过滤）
- _PROVIDER_CLASSES（7 个 provider 工厂完整性）
- _config（配置解析）
- _message_tracking_ref（去重引用生成）
"""

from __future__ import annotations

from typing import Any

import pytest

import mail_provider


# --------------------------------------------------------------------------- #
# _random_mailbox_name / _random_subdomain_label
# --------------------------------------------------------------------------- #
class TestRandomNames:
    def test_mailbox_name_format(self) -> None:
        name = mail_provider._random_mailbox_name()
        # 5 字母 + 1-3 数字 + 1-3 字母 = 最短 7、最长 11
        assert 7 <= len(name) <= 11
        assert name.islower()
        assert name.isascii()

    def test_mailbox_name_randomness(self) -> None:
        names = {mail_provider._random_mailbox_name() for _ in range(50)}
        # 随机性足够，50 次调用不应全相同
        assert len(names) > 1

    def test_subdomain_label_format(self) -> None:
        for _ in range(20):
            label = mail_provider._random_subdomain_label()
            assert 4 <= len(label) <= 10
            assert all(c.isascii() and c.isalnum() for c in label)
            assert label == label.lower()


# --------------------------------------------------------------------------- #
# _next_domain
# --------------------------------------------------------------------------- #
class TestNextDomain:
    def test_single_domain(self) -> None:
        assert mail_provider._next_domain(["a.com"]) == "a.com"

    def test_multiple_domains_round_robin(self) -> None:
        domains = ["a.com", "b.com", "c.com"]
        sequence = [mail_provider._next_domain(domains) for _ in range(6)]
        assert sequence == ["a.com", "b.com", "c.com", "a.com", "b.com", "c.com"]

    def test_empty_list_raises(self) -> None:
        with pytest.raises(RuntimeError):
            mail_provider._next_domain([])


# --------------------------------------------------------------------------- #
# _extract_code + _FORBIDDEN_CODES
# --------------------------------------------------------------------------- #
class TestExtractCode:
    def test_styled_code_block(self) -> None:
        msg = {
            "subject": "Verify",
            "text_content": "",
            "html_content": '<p style="background-color:#F3F3F3">123456</p>',
        }
        assert mail_provider._extract_code(msg) == "123456"

    def test_verification_code_prefix(self) -> None:
        msg = {"subject": "", "text_content": "Your verification code: 654321", "html_content": ""}
        assert mail_provider._extract_code(msg) == "654321"

    def test_code_is_prefix(self) -> None:
        msg = {"subject": "", "text_content": "Your code is 111222", "html_content": ""}
        assert mail_provider._extract_code(msg) == "111222"

    def test_chinese_prefix(self) -> None:
        msg = {"subject": "", "text_content": "验证码：998877", "html_content": ""}
        assert mail_provider._extract_code(msg) == "998877"

    def test_html_wrapped_digits(self) -> None:
        msg = {"subject": "", "text_content": "", "html_content": "<span>456789</span>"}
        assert mail_provider._extract_code(msg) == "456789"

    def test_bare_digits(self) -> None:
        msg = {"subject": "", "text_content": "Hello 246813 world", "html_content": ""}
        assert mail_provider._extract_code(msg) == "246813"

    def test_html_entity_not_matched(self) -> None:
        # &#177010; 不应被当作 6 位数字匹配（& 前缀排除）
        msg = {"subject": "", "text_content": "code &#177010; here 123456", "html_content": ""}
        # 177010 在 _FORBIDDEN_CODES，且 & 前缀排除；最终命中 123456
        assert mail_provider._extract_code(msg) == "123456"

    def test_color_value_not_matched(self) -> None:
        # #123456 颜色值不应匹配（# 前缀排除）
        msg = {"subject": "", "text_content": "color #123456 then 987654", "html_content": ""}
        assert mail_provider._extract_code(msg) == "987654"

    def test_forbidden_code_skipped(self) -> None:
        msg = {"subject": "", "text_content": "verification code 177010", "html_content": ""}
        assert mail_provider._extract_code(msg) is None

    def test_empty_message(self) -> None:
        msg = {"subject": "", "text_content": "", "html_content": ""}
        assert mail_provider._extract_code(msg) is None

    def test_no_six_digit_code(self) -> None:
        msg = {"subject": "Hello", "text_content": "short msg 123", "html_content": ""}
        assert mail_provider._extract_code(msg) is None

    def test_forbidden_codes_contains_openai_sentinel(self) -> None:
        assert "177010" in mail_provider._FORBIDDEN_CODES


# --------------------------------------------------------------------------- #
# _PROVIDER_CLASSES 工厂完整性
# --------------------------------------------------------------------------- #
class TestProviderFactory:
    EXPECTED_TYPES = {
        "cloudflare_temp_email",
        "tempmail_lol",
        "duckmail",
        "gptmail",
        "moemail",
        "inbucket",
        "yyds_mail",
    }

    def test_all_seven_providers_registered(self) -> None:
        assert set(mail_provider._PROVIDER_CLASSES.keys()) == self.EXPECTED_TYPES

    def test_all_providers_extend_base(self) -> None:
        base = mail_provider.BaseMailProvider
        for name, cls in mail_provider._PROVIDER_CLASSES.items():
            assert issubclass(cls, base), f"{name} 不是 BaseMailProvider 子类"

    def test_all_providers_have_name_attribute(self) -> None:
        for name, cls in mail_provider._PROVIDER_CLASSES.items():
            assert hasattr(cls, "name"), f"{name} 缺少 name 属性"
            # name 属性应与注册 key 一致（或至少非空）
            assert getattr(cls, "name", "") != ""


# --------------------------------------------------------------------------- #
# _config
# --------------------------------------------------------------------------- #
class TestConfigHelper:
    def test_default_values(self) -> None:
        cfg = mail_provider._config({"providers": []})
        assert cfg["request_timeout"] == 30
        assert cfg["wait_timeout"] == 60
        assert cfg["wait_interval"] == 2
        assert "user_agent" in cfg

    def test_custom_values(self) -> None:
        cfg = mail_provider._config(
            {
                "providers": [],
                "request_timeout": 15,
                "wait_timeout": 90,
                "wait_interval": 5,
            }
        )
        assert cfg["request_timeout"] == 15
        assert cfg["wait_timeout"] == 90
        assert cfg["wait_interval"] == 5


# --------------------------------------------------------------------------- #
# _message_tracking_ref（去重引用生成）
# --------------------------------------------------------------------------- #
class TestMessageTrackingRef:
    def test_uses_message_id_when_present(self) -> None:
        msg = {
            "provider": "duckmail",
            "mailbox": "user@example.com",
            "message_id": "abc-123",
        }
        ref = mail_provider._message_tracking_ref(msg)
        assert ref == "id:duckmail:user@example.com:abc-123"

    def test_uses_content_hash_when_no_id(self) -> None:
        msg = {
            "provider": "duckmail",
            "mailbox": "user@example.com",
            "received_at": "2024-01-01T00:00:00",
            "subject": "Test",
            "sender": "noreply@example.com",
            "text_content": "Hello",
            "html_content": "<p>Hello</p>",
        }
        ref = mail_provider._message_tracking_ref(msg)
        assert ref.startswith("content:duckmail:user@example.com:")
        # 内容相同 → hash 相同
        ref2 = mail_provider._message_tracking_ref(msg)
        assert ref == ref2

    def test_different_content_different_hash(self) -> None:
        base = {
            "provider": "duckmail",
            "mailbox": "user@example.com",
            "subject": "Test",
            "sender": "noreply@example.com",
            "text_content": "",
            "html_content": "",
        }
        ref1 = mail_provider._message_tracking_ref({**base, "text_content": "A"})
        ref2 = mail_provider._message_tracking_ref({**base, "text_content": "B"})
        assert ref1 != ref2


# --------------------------------------------------------------------------- #
# _enabled_entries / _next_entry（工厂轮询逻辑）
# --------------------------------------------------------------------------- #
class TestEntryHelpers:
    @staticmethod
    def _make_config(types: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {"providers": [{"type": t, "enable": True} for t in types]}

    def test_enabled_entries_filters_disabled(self) -> None:
        mail_config = {
            "providers": [
                {"type": "duckmail", "enable": True},
                {"type": "gptmail", "enable": False},
                {"type": "moemail", "enable": True},
            ]
        }
        enabled = mail_provider._enabled_entries(mail_config)
        assert len(enabled) == 2
        assert enabled[0]["type"] == "duckmail"
        assert enabled[1]["type"] == "moemail"

    def test_no_enabled_raises(self) -> None:
        mail_config = {"providers": [{"type": "duckmail", "enable": False}]}
        with pytest.raises(RuntimeError):
            mail_provider._enabled_entries(mail_config)

    def test_next_entry_single(self) -> None:
        cfg = self._make_config(["duckmail"])
        entry = mail_provider._next_entry(cfg)
        assert entry["type"] == "duckmail"
        assert entry["provider_ref"] == "duckmail#1"

    def test_next_entry_round_robin(self) -> None:
        cfg = self._make_config(["duckmail", "gptmail", "moemail"])
        sequence = [mail_provider._next_entry(cfg)["type"] for _ in range(6)]
        assert sequence == ["duckmail", "gptmail", "moemail"] * 2


# --------------------------------------------------------------------------- #
# 域名运行时控制：disable / cooldown / fail-counter
# --------------------------------------------------------------------------- #
class TestDomainControl:
    def test_configure_domain_control_updates_threshold_and_cooldown(self) -> None:
        mail_provider.configure_domain_control(fail_threshold=5, fail_cooldown_sec=600)
        status = mail_provider.get_domain_status()
        assert status["fail_threshold"] == 5
        assert status["fail_cooldown_sec"] == 600

    def test_configure_domain_control_clamps_to_minimum(self) -> None:
        mail_provider.configure_domain_control(fail_threshold=0, fail_cooldown_sec=-10)
        status = mail_provider.get_domain_status()
        assert status["fail_threshold"] == 1
        assert status["fail_cooldown_sec"] == 0

    def test_disable_and_enable_domain(self) -> None:
        mail_provider.disable_domain("a.com")
        status = mail_provider.get_domain_status()
        assert status["domains"]["a.com"]["disabled"] is True

        mail_provider.enable_domain("a.com")
        status = mail_provider.get_domain_status()
        assert "a.com" not in status["domains"]

    def test_enable_domain_clears_fail_counter_and_cooldown(self) -> None:
        mail_provider.configure_domain_control(fail_threshold=1, fail_cooldown_sec=300)
        mail_provider.record_domain_fail("b.com")

        mail_provider.enable_domain("b.com")
        mail_provider.configure_domain_control(fail_threshold=10)
        mail_provider.record_domain_fail("b.com")
        status = mail_provider.get_domain_status()
        assert status["domains"].get("b.com", {}).get("fail_count", 0) == 1
        assert status["domains"].get("b.com", {}).get("cooling") is False

    def test_record_domain_fail_below_threshold_does_not_cool(self) -> None:
        mail_provider.configure_domain_control(fail_threshold=3, fail_cooldown_sec=300)
        mail_provider.record_domain_fail("c.com")
        mail_provider.record_domain_fail("c.com")
        status = mail_provider.get_domain_status()
        assert status["domains"]["c.com"]["fail_count"] == 2
        assert status["domains"]["c.com"]["cooling"] is False

    def test_record_domain_fail_at_threshold_triggers_cooldown(self) -> None:
        mail_provider.configure_domain_control(fail_threshold=2, fail_cooldown_sec=300)
        mail_provider.record_domain_fail("d.com")
        mail_provider.record_domain_fail("d.com")
        status = mail_provider.get_domain_status()
        assert status["domains"]["d.com"]["fail_count"] == 2
        assert status["domains"]["d.com"]["cooling"] is True
        assert 0 < status["domains"]["d.com"]["cooldown_remaining"] <= 300

    def test_record_domain_success_clears_fail_counter(self) -> None:
        mail_provider.configure_domain_control(fail_threshold=5, fail_cooldown_sec=300)
        mail_provider.record_domain_fail("e.com")
        mail_provider.record_domain_success("e.com")
        status = mail_provider.get_domain_status()
        assert "e.com" not in status["domains"]

    def test_record_domain_fail_ignores_empty(self) -> None:
        mail_provider.record_domain_fail("")
        mail_provider.record_domain_fail("   ")
        status = mail_provider.get_domain_status()
        assert status["domains"] == {}

    def test_reset_domain_state_clears_everything(self) -> None:
        mail_provider.configure_domain_control(fail_threshold=1, fail_cooldown_sec=60)
        mail_provider.disable_domain("f.com")
        mail_provider.record_domain_fail("g.com")

        mail_provider.reset_domain_state()
        status = mail_provider.get_domain_status()
        assert status["domains"] == {}
        assert status["fail_threshold"] == 3
        assert status["fail_cooldown_sec"] == 300

    def test_get_domain_status_cooldown_remaining_decreases(self) -> None:
        mail_provider.configure_domain_control(fail_threshold=1, fail_cooldown_sec=600)
        mail_provider.record_domain_fail("h.com")
        first = mail_provider.get_domain_status()["domains"]["h.com"]["cooldown_remaining"]
        import time as _time

        _time.sleep(0.05)
        second = mail_provider.get_domain_status()["domains"]["h.com"]["cooldown_remaining"]
        assert second < first


class TestNextDomainFiltering:
    def test_next_domain_skips_disabled(self) -> None:
        mail_provider.disable_domain("a.com")
        result = mail_provider._next_domain(["a.com", "b.com"])
        assert result == "b.com"

    def test_next_domain_skips_cooling(self) -> None:
        mail_provider.configure_domain_control(fail_threshold=1, fail_cooldown_sec=300)
        mail_provider.record_domain_fail("a.com")
        result = mail_provider._next_domain(["a.com", "b.com"])
        assert result == "b.com"

    def test_next_domain_all_disabled_raises(self) -> None:
        mail_provider.disable_domain("a.com")
        mail_provider.disable_domain("b.com")
        with pytest.raises(RuntimeError, match="禁用"):
            mail_provider._next_domain(["a.com", "b.com"])

    def test_next_domain_all_cooling_raises(self) -> None:
        mail_provider.configure_domain_control(fail_threshold=1, fail_cooldown_sec=300)
        mail_provider.record_domain_fail("a.com")
        mail_provider.record_domain_fail("b.com")
        with pytest.raises(RuntimeError, match="冷却"):
            mail_provider._next_domain(["a.com", "b.com"])

    def test_next_domain_round_robin_among_available_only(self) -> None:
        mail_provider.disable_domain("c.com")
        result = [mail_provider._next_domain(["a.com", "b.com", "c.com"]) for _ in range(4)]
        assert all(r in {"a.com", "b.com"} for r in result)
        assert "c.com" not in result
        assert "a.com" in result
        assert "b.com" in result


# --------------------------------------------------------------------------- #
# 多级子域名生成
# --------------------------------------------------------------------------- #
class TestSubdomainChain:
    def test_build_subdomain_chain_levels_1(self) -> None:
        result = mail_provider._build_subdomain_chain("example.com", levels=1)
        parts = result.split(".")
        assert parts[-1] == "com"
        assert parts[-2] == "example"
        assert len(parts) == 3

    def test_build_subdomain_chain_levels_3(self) -> None:
        result = mail_provider._build_subdomain_chain("example.com", levels=3)
        parts = result.split(".")
        assert parts[-1] == "com"
        assert parts[-2] == "example"
        assert len(parts) == 5

    def test_build_subdomain_chain_levels_0_returns_base(self) -> None:
        result = mail_provider._build_subdomain_chain("example.com", levels=0)
        assert result == "example.com"

    def test_build_subdomain_chain_empty_base_raises(self) -> None:
        with pytest.raises(RuntimeError):
            mail_provider._build_subdomain_chain("", levels=1)

    def test_build_subdomain_chain_random_levels_within_range(self) -> None:
        for _ in range(20):
            result = mail_provider._build_subdomain_chain("example.com", levels=3, random_levels=True)
            label_count = len(result.split(".")) - 2
            assert 1 <= label_count <= 3

    def test_generate_mailbox_address_default(self) -> None:
        addr = mail_provider.generate_mailbox_address("example.com")
        assert "@" in addr
        user, _, domain = addr.partition("@")
        assert user != ""
        assert domain.endswith("example.com")
        assert len(domain.split(".")) >= 3

    def test_generate_mailbox_address_with_username(self) -> None:
        addr = mail_provider.generate_mailbox_address("example.com", username="alice")
        assert addr.startswith("alice@")
        assert addr.endswith("example.com")

    def test_generate_mailbox_address_sub_levels_0_no_subdomain(self) -> None:
        addr = mail_provider.generate_mailbox_address("example.com", sub_levels=0)
        _, _, domain = addr.partition("@")
        assert domain == "example.com"

    def test_generate_mailbox_address_empty_domain_raises(self) -> None:
        with pytest.raises(RuntimeError):
            mail_provider.generate_mailbox_address("")

    def test_generate_mailbox_address_random_levels(self) -> None:
        for _ in range(20):
            addr = mail_provider.generate_mailbox_address(
                "example.com", sub_levels=3, random_levels=True
            )
            label_count = len(addr.split("@")[1].split(".")) - 2
            assert 1 <= label_count <= 3

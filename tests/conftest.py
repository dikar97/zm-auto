"""pytest 共享 fixture。

当前项目的核心模块（mail_provider / captcha_solver / sub2api_importer）
都使用模块级全局状态（轮询索引、锁），测试间需要重置以避免相互干扰。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_mail_provider_state() -> Iterator[None]:
    """每个 mail_provider 测试前后重置模块级轮询计数器与域名运行时状态。"""
    import mail_provider

    original_provider_index = mail_provider.provider_index
    original_domain_index = mail_provider.domain_index
    mail_provider.provider_index = 0
    mail_provider.domain_index = 0
    mail_provider.reset_domain_state()
    yield
    mail_provider.provider_index = original_provider_index
    mail_provider.domain_index = original_domain_index
    mail_provider.reset_domain_state()

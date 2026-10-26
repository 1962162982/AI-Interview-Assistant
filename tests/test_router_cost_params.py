"""路由级成本参数测试：thinking 关闭与 max_tokens 上限是否正确下发。

对应 2026-10-26 成本优化（docs/decision_log.md）：
- ``backend/config/models.deepseek.yaml`` 为临时全 DeepSeek 配置，默认关闭深度思考并限制输出；
- ``ModelRouter._build_kwargs`` 需通过 ``extra_body`` 透传 ``thinking`` 等供应商参数。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from backend.main import ThinkingModeMiddleware
from backend.services.model_router import (
    THINKING_BUDGET_TOKENS,
    THINKING_TOKEN_RESERVE,
    ModelRouter,
    get_thinking_override,
    load_route_configs,
    reset_thinking_override,
    set_thinking_override,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEEPSEEK_CONFIG = REPO_ROOT / "backend" / "config" / "models.deepseek.yaml"


def test_deepseek_config_disables_thinking_and_sets_max_tokens() -> None:
    configs = load_route_configs(DEEPSEEK_CONFIG)
    assert set(configs) == {
        "resume_diagnosis",
        "jd_matching",
        "interview_agent",
        "evaluation",
        "cross_review",
    }
    for name, config in configs.items():
        assert config.extra.get("thinking") == {"type": "disabled"}, name
        assert isinstance(config.max_tokens, int) and config.max_tokens > 0, name


def test_build_kwargs_uses_route_defaults_and_extra_body() -> None:
    router = ModelRouter(
        config_path=DEEPSEEK_CONFIG,
        load_env=False,
        client_factory=lambda *args, **kwargs: None,
    )
    config = router.get_route_config("resume_diagnosis")
    kwargs = router._build_kwargs(
        config,
        [{"role": "user", "content": "只回复 OK"}],
        temperature=None,
        max_tokens=None,
        response_format=None,
        extra=None,
        stream=False,
    )
    assert kwargs["max_tokens"] == config.max_tokens
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    # 调用级 max_tokens / extra 应覆盖并合并路由默认值
    overridden = router._build_kwargs(
        config,
        [{"role": "user", "content": "只回复 OK"}],
        temperature=None,
        max_tokens=64,
        response_format={"type": "json_object"},
        extra={"effort": "low"},
        stream=False,
    )
    assert overridden["max_tokens"] == 64
    assert overridden["response_format"] == {"type": "json_object"}
    assert overridden["extra_body"] == {"thinking": {"type": "disabled"}, "effort": "low"}


def test_thinking_override_contextvar_wins_over_config() -> None:
    router = ModelRouter(
        config_path=DEEPSEEK_CONFIG,
        load_env=False,
        client_factory=lambda *args, **kwargs: None,
    )
    config = router.get_route_config("resume_diagnosis")

    token = set_thinking_override(True)
    try:
        kwargs = router._build_kwargs(
            config,
            [{"role": "user", "content": "只回复 OK"}],
            temperature=None,
            max_tokens=None,
            response_format=None,
            extra=None,
            stream=False,
        )
    finally:
        reset_thinking_override(token)
    assert kwargs["extra_body"]["thinking"] == {
        "type": "enabled",
        "budget_tokens": THINKING_BUDGET_TOKENS,
    }
    assert kwargs["extra_body"]["effort"] == "high"
    assert kwargs["max_tokens"] == config.max_tokens + THINKING_TOKEN_RESERVE

    token = set_thinking_override(False)
    try:
        kwargs = router._build_kwargs(
            config,
            [{"role": "user", "content": "只回复 OK"}],
            temperature=None,
            max_tokens=None,
            response_format=None,
            extra=None,
            stream=False,
        )
    finally:
        reset_thinking_override(token)
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
    assert kwargs["max_tokens"] == config.max_tokens


def test_thinking_middleware_maps_header_to_contextvar() -> None:
    captured: list[bool | None] = []

    async def fake_app(scope, receive, send):
        captured.append(get_thinking_override())

    async def run_with_header(header: bytes) -> None:
        scope = {"type": "http", "headers": [(b"x-thinking-mode", header)]}
        await ThinkingModeMiddleware(fake_app)(scope, None, None)

    asyncio.run(run_with_header(b"enabled"))
    assert captured[-1] is True

    asyncio.run(run_with_header(b"disabled"))
    assert captured[-1] is False

    asyncio.run(run_with_header(b""))
    assert captured[-1] is None

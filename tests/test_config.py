from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_overlord.config import AppConfig


def test_loads_tmux_watcher_compatible_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """hosts:
  - name: laptop
    local: true
  - name: work
    ssh: user@example.test
    port: 2222
    key: /tmp/key
poll_interval_secs: 3
attention_patterns:
  - Permission
quick_actions:
  - label: yes
    keys: "yes\\n"
""",
        encoding="utf-8",
    )
    config = AppConfig.load(path)
    assert [host.name for host in config.hosts] == ["laptop", "work"]
    assert config.hosts[1].port == 2222
    assert config.poll_interval_secs == 3
    assert config.attention_patterns == ["Permission"]
    assert config.automation.automation_enabled is False
    assert config.automation.dry_run is True


def test_host_requires_transport(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("hosts:\n  - name: nowhere\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        AppConfig.load(path)


def test_controller_gateway_must_remain_loopback() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        AppConfig.model_validate(
            {
                "hosts": [{"name": "local", "local": True}],
                "controller_runtime_enabled": True,
                "controller_mcp_url": "http://0.0.0.0:8001",
            }
        )

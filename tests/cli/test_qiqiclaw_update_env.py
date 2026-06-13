from types import SimpleNamespace

from qiqiclaw_cli import main as qiqiclaw_main


def test_update_dependency_env_detects_gitee_origin_and_sets_domestic_mirrors():
    env = qiqiclaw_main._update_dependency_env(
        "git@gitee.com:szd20020329/qiqiclaw.git",
        {"PATH": "/usr/bin"},
    )

    assert env["QIQICLAW_INSTALL_SOURCE"] == "gitee"
    assert env["HERMES_INSTALL_SOURCE"] == "gitee"
    assert env["PIP_INDEX_URL"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert env["UV_INDEX_URL"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert env["UV_DEFAULT_INDEX"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert env["npm_config_registry"] == "https://registry.npmmirror.com"
    assert env["QIQICLAW_NODE_DIST_BASE_URL"] == "https://registry.npmmirror.com/-/binary/node"
    assert env["PATH"] == "/usr/bin"


def test_update_dependency_env_defaults_to_github_without_mirror_overrides():
    env = qiqiclaw_main._update_dependency_env(
        "git@github.com:xzly111/qiqiclaw.git",
        {"PATH": "/usr/bin"},
    )

    assert env["QIQICLAW_INSTALL_SOURCE"] == "github"
    assert env["HERMES_INSTALL_SOURCE"] == "github"
    assert "PIP_INDEX_URL" not in env
    assert "npm_config_registry" not in env


def test_update_dependency_env_explicit_source_overrides_origin():
    env = qiqiclaw_main._update_dependency_env(
        "git@github.com:xzly111/qiqiclaw.git",
        {"QIQICLAW_INSTALL_SOURCE": "gitee"},
    )

    assert env["QIQICLAW_INSTALL_SOURCE"] == "gitee"
    assert env["UV_DEFAULT_INDEX"] == "https://pypi.tuna.tsinghua.edu.cn/simple"


def test_resolve_update_branch_defaults_to_main_and_accepts_override():
    assert qiqiclaw_main._resolve_update_branch(SimpleNamespace()) == "main"
    assert qiqiclaw_main._resolve_update_branch(SimpleNamespace(branch="")) == "main"
    assert qiqiclaw_main._resolve_update_branch(SimpleNamespace(branch="release/2.0")) == "release/2.0"

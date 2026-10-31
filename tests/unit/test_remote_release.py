import os
import stat
import tarfile
from pathlib import Path

import pytest

from scripts import deploy_linux_lab, package_release


def test_archive_excludes_local_env_keys_caches_and_symlinks(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for name in package_release.ROOT_FILES:
        (root / name).write_text("example only")
    (root / ".env").write_text("secret-env")
    (root / "src").mkdir()
    (root / "src/main.py").write_text("pass")
    (root / "src/.env").write_text("nested-secret")
    (root / "src/key.pem").write_text("private-key")
    (root / "src/__pycache__").mkdir()
    (root / "src/__pycache__/main.pyc").write_bytes(b"cached")
    output = tmp_path / "release.tar.gz"
    count, digest = package_release.package(root, output)
    with tarfile.open(output) as archive:
        assert set(archive.getnames()) == package_release.ROOT_FILES | {"src/main.py"}
        assert all(member.uid == 0 and member.mode == 0o644 for member in archive.getmembers())
    assert count == len(package_release.ROOT_FILES) + 1 and len(digest) == 64
    with pytest.raises(FileExistsError):
        package_release.package(root, output)
    (root / "src/leak.py").symlink_to(root / ".env")
    with pytest.raises(ValueError, match="symlink forbidden"):
        package_release.package(root, tmp_path / "bad.tar.gz")
    assert not (tmp_path / "bad.tar.gz").exists()


def test_generated_remote_credentials_are_new_private_and_never_overwritten(tmp_path, capsys):
    path = tmp_path / ".env"
    deploy_linux_lab.initialize_env(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    original = path.read_bytes()
    values = dict(line.split("=", 1) for line in original.decode().splitlines())
    credentials = [
        values[key]
        for key in (
            "DBIC_PG_PASSWORD",
            "DBIC_API_TOKEN",
            "MYSQL_ROOT_PASSWORD",
            "MYSQL_DIAG_PASSWORD",
        )
    ]
    assert len(set(credentials)) == 4
    assert all(len(value) == 64 for value in credentials)
    assert values["DBIC_SEARCH_BACKEND"] == values["DBIC_METRICS_BACKEND"] == "null"
    assert not capsys.readouterr().out
    with pytest.raises(FileExistsError):
        deploy_linux_lab.initialize_env(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize("kind", ["container", "volume", "network"])
def test_preflight_refuses_existing_project_without_mutating(monkeypatch, tmp_path, kind):
    monkeypatch.setattr(deploy_linux_lab.platform, "system", lambda: "Linux")
    monkeypatch.setattr(deploy_linux_lab, "ROOT", tmp_path)
    monkeypatch.delenv("DOCKER_HOST", raising=False)

    def capture(command):
        if command[1] == "context":
            return "unix:///var/run/docker.sock"
        target = {"container": "ps", "volume": "volume", "network": "network"}[kind]
        return "existing-resource" if command[1] == target else ""

    monkeypatch.setattr(deploy_linux_lab, "capture", capture)
    with pytest.raises(RuntimeError, match=f"existing dbic-lab {kind}"):
        deploy_linux_lab.preflight()
    assert not (tmp_path / ".env").exists()


def test_preflight_rejects_remote_docker_context(monkeypatch):
    monkeypatch.setattr(deploy_linux_lab.platform, "system", lambda: "Linux")
    monkeypatch.setattr(deploy_linux_lab, "capture", lambda command: "ssh://other-host")
    with pytest.raises(RuntimeError, match="local Docker Unix socket"):
        deploy_linux_lab.preflight()


def test_deploy_orders_bootstrap_before_api_and_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy_linux_lab, "ROOT", tmp_path)
    commands = []
    monkeypatch.setattr(
        deploy_linux_lab.subprocess, "run", lambda command, **kwargs: commands.append(command)
    )
    deploy_linux_lab.deploy()
    assert len(commands) == 5
    assert commands[1][-2:] == ["dbic-postgres", "mysql-lab"]
    assert commands[2][-1] == "scripts/setup_mysql_lab.py"
    assert commands[3][-1] == "dbic-api"
    assert commands[4][-1] == "scripts/verify_m2.py"
    assert all(not any(arg in {"down", "rm", "prune"} for arg in command) for command in commands)


def test_linux_override_changes_only_api_host_mapping():
    import yaml

    override = yaml.safe_load(Path("deploy/compose.linux.yaml").read_text())
    assert override == {
        "services": {"dbic-api": {"extra_hosts": ["host.docker.internal:host-gateway"]}}
    }


def test_ambient_configuration_is_rejected(monkeypatch):
    monkeypatch.setattr(deploy_linux_lab.sys, "argv", ["deploy_linux_lab.py", "--deploy"])
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "unrelated")
    cwd = os.getcwd()
    try:
        with pytest.raises(RuntimeError, match="unset inherited"):
            deploy_linux_lab.main()
    finally:
        os.chdir(cwd)

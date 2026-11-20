import pytest

from douyin_live_sense.auth import AuthStore
from douyin_live_sense.cli import main


def test_init_auth_prints_once_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DLS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DLS_ADMIN_PASSWORD", "auto-password-1234")
    assert main(["init-auth"]) == 0
    assert "auto-password-1234" in capsys.readouterr().out
    assert main(["init-auth"]) == 0
    assert "已存在" in capsys.readouterr().out
    auth = AuthStore(tmp_path / "auth.json")
    assert auth.verify("auto-password-1234")


def test_server_requires_auth_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DLS_DATA_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 1
    assert "setup.sh" in capsys.readouterr().err


def test_reset_password_changes_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DLS_DATA_DIR", str(tmp_path))
    main(["init-auth", "--password", "old-password-1234"])
    capsys.readouterr()
    assert main(["reset-password", "--password", "new-password-5678"]) == 0
    assert AuthStore(tmp_path / "auth.json").verify("new-password-5678")

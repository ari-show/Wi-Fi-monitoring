import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from systemd import configure


def test_generate_units_uses_clone_path_and_selected_python(tmp_path):
    root = tmp_path / 'clone with spaces %h $HOME "quoted"'
    root.mkdir()
    shutil.copytree(
        Path(configure.__file__).parent,
        root / "systemd",
        ignore=shutil.ignore_patterns("generated", "__pycache__"),
    )
    python = root / ".venv" / "bin" / "python"
    output = configure.generate_units(root, python, 54321)
    monitor = (output / "netwatch.service").read_text()
    viewer = (output / "netwatch_view.service").read_text()
    assert f"WorkingDirectory={str(root).replace('%', '%%')}/\n" in monitor
    assert (
        f"ExecStart={configure.unit_path(python, command=True)} -u main.py" in monitor
    )
    assert "--log netwatch.csv" in monitor
    assert "--host 0.0.0.0 --port 54321" in viewer
    assert "@WORKING_DIRECTORY@" not in viewer
    assert "@PYTHON@" not in viewer
    assert "%%h" in monitor
    assert 'ExecStart=":/' in monitor
    assert "$HOME" in monitor.split("ExecStart=")[1]
    assert "$$HOME" not in monitor
    assert '\\"quoted\\"' in monitor


@pytest.mark.parametrize(
    "value", ["/tmp/bad\npath", "/tmp/bad\rpath", "/tmp/bad\x00path"]
)
def test_unit_paths_reject_control_characters(value):
    with pytest.raises(ValueError):
        configure.unit_path(value)


@pytest.mark.parametrize("requested", [None, 54321])
def test_choose_port_checks_same_bind_address_as_viewer(monkeypatch, requested):
    probe = MagicMock()
    probe.__enter__.return_value = probe
    probe.getsockname.return_value = ("0.0.0.0", 54321)
    monkeypatch.setattr(configure.socket, "socket", lambda *args: probe)
    assert configure.choose_port(requested) == 54321
    probe.bind.assert_called_once_with(("0.0.0.0", requested or 0))
    probe.__exit__.assert_called_once()


def test_dynamic_port_is_chosen_at_service_start():
    # conftest blocks sockets: generation must not reserve one in this mode.
    assert configure.choose_port(0) == 0


def test_busy_port_fails_without_generating_units(monkeypatch, tmp_path):
    probe = MagicMock()
    probe.__enter__.return_value = probe
    probe.bind.side_effect = OSError("Address already in use")
    monkeypatch.setattr(configure.socket, "socket", lambda *args: probe)
    monkeypatch.setattr(
        configure, "__file__", str(tmp_path / "systemd" / "configure.py")
    )
    monkeypatch.setattr(configure.sys, "argv", ["configure.py", "--port", "54321"])
    with pytest.raises(SystemExit) as exc:
        configure.main()
    assert exc.value.code == 1
    assert not (tmp_path / "systemd" / "generated").exists()


@pytest.mark.parametrize("port", ["-1", "65536"])
def test_configure_rejects_invalid_ports(monkeypatch, port):
    monkeypatch.setattr(configure.sys, "argv", ["configure.py", "--port", port])
    with pytest.raises(SystemExit) as exc:
        configure.main()
    assert exc.value.code == 2

from unittest.mock import MagicMock, Mock

import pytest

import netwatch_view as view


@pytest.mark.parametrize("requested", ["0", "54321"])
def test_view_reports_bound_port_and_closes_server(monkeypatch, capsys, requested):
    server = MagicMock()
    server.server_address = ("0.0.0.0", 54321)
    server.serve_forever.side_effect = KeyboardInterrupt
    factory = Mock(return_value=server)
    monkeypatch.setattr(view.http.server, "ThreadingHTTPServer", factory)
    monkeypatch.setattr(
        "sys.argv", ["netwatch_view.py", "netwatch.csv", "--port", requested]
    )
    view.main()
    assert factory.call_args.args[0] == ("0.0.0.0", int(requested))
    assert "http://<この端末のLAN IP>:54321/" in capsys.readouterr().out
    server.__exit__.assert_called_once()


def test_view_does_not_announce_url_if_bind_fails(monkeypatch, capsys):
    factory = Mock(side_effect=OSError("Address already in use"))
    monkeypatch.setattr(view.http.server, "ThreadingHTTPServer", factory)
    monkeypatch.setattr("sys.argv", ["netwatch_view.py", "netwatch.csv"])
    with pytest.raises(SystemExit) as exc:
        view.main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "待受を開始できませんでした" in captured.err


@pytest.mark.parametrize("port", ["-1", "65536"])
def test_view_rejects_invalid_ports(monkeypatch, port):
    monkeypatch.setattr(
        "sys.argv", ["netwatch_view.py", "netwatch.csv", "--port", port]
    )
    with pytest.raises(SystemExit) as exc:
        view.main()
    assert exc.value.code == 2

import socket
import struct
import subprocess
from unittest.mock import MagicMock, Mock, mock_open

import pytest

import main


def test_run_returns_stdout(monkeypatch):
    process = Mock(return_value=subprocess.CompletedProcess(["ping"], 0, "reply", ""))
    monkeypatch.setattr(main.subprocess, "run", process)
    assert main.run(["ping"], timeout=3) == "reply"
    process.assert_called_once_with(["ping"], capture_output=True, text=True, timeout=3)


@pytest.mark.parametrize(
    "error", [FileNotFoundError(), subprocess.TimeoutExpired("ping", 3)]
)
def test_run_handles_command_failures(monkeypatch, error):
    monkeypatch.setattr(main.subprocess, "run", Mock(side_effect=error))
    assert main.run(["ping"]) == ""


@pytest.mark.parametrize(("is_mac", "wait"), [(True, "1000"), (False, "1")])
@pytest.mark.parametrize(
    ("output", "rtt"),
    [("64 bytes: time=12.3 ms", 12.3), ("time<1 ms", 1.0), ("Request timeout", None)],
)
def test_ping_parsing_and_platform_timeout(monkeypatch, is_mac, wait, output, rtt):
    run = Mock(return_value=output)
    monkeypatch.setattr(main, "run", run)
    monkeypatch.setattr(main, "IS_MAC", is_mac)
    assert main.ping_once("192.0.2.1") == rtt
    run.assert_called_once_with(["ping", "-c", "1", "-n", "-W", wait, "192.0.2.1"], 3)


@pytest.mark.parametrize(
    ("is_mac", "output", "gateway", "iface", "command"),
    [
        (
            True,
            "gateway: 192.0.2.1\ninterface: en0\n",
            "192.0.2.1",
            "en0",
            ["route", "-n", "get", "default"],
        ),
        (
            False,
            "default via 192.0.2.1 dev wlan0 proto dhcp",
            "192.0.2.1",
            "wlan0",
            ["ip", "route", "show", "default"],
        ),
        (True, "", None, None, ["route", "-n", "get", "default"]),
        (False, "", None, None, ["ip", "route", "show", "default"]),
    ],
)
def test_default_route(monkeypatch, is_mac, output, gateway, iface, command):
    run = Mock(return_value=output)
    monkeypatch.setattr(main, "run", run)
    monkeypatch.setattr(main, "IS_MAC", is_mac)
    assert main.default_gateway() == gateway
    assert main.default_iface() == iface
    assert all(call.args == (command,) for call in run.call_args_list)


@pytest.mark.parametrize(("is_mac", "flag"), [(True, "-m"), (False, "-t")])
def test_hops_preserve_timeouts_and_stop_at_destination(monkeypatch, is_mac, flag):
    run = Mock(
        side_effect=[
            "From 192.0.2.1 Time exceeded",
            "",
            "64 bytes from 1.1.1.1: time=10 ms",
        ]
    )
    monkeypatch.setattr(main, "run", run)
    monkeypatch.setattr(main, "IS_MAC", is_mac)
    assert main.hop_addrs("1.1.1.1") == ["192.0.2.1", None, "1.1.1.1"]
    assert run.call_count == 3
    for ttl, call in enumerate(run.call_args_list, 1):
        assert call.args[0][-3:] == [flag, str(ttl), "1.1.1.1"]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("signal: -65 dBm\ntx bitrate: 72.2 MBit/s", (-65, 72.2)),
        ("signal: -80 dBm", (-80, None)),
        ("Not connected.", None),
    ],
)
def test_wifi_info(monkeypatch, output, expected):
    monkeypatch.setattr(main, "IS_MAC", False)
    monkeypatch.setattr(main, "run", Mock(return_value=output))
    assert main.wifi_info("wlan0") == expected


@pytest.mark.parametrize(("is_mac", "iface"), [(True, "en0"), (False, None)])
def test_wifi_unsupported_does_not_run_commands(monkeypatch, is_mac, iface):
    run = Mock(side_effect=AssertionError("Unexpected command"))
    monkeypatch.setattr(main, "IS_MAC", is_mac)
    monkeypatch.setattr(main, "run", run)
    assert main.wifi_info(iface) is None
    run.assert_not_called()


def test_resolvers_parse_ipv4_and_ipv6(monkeypatch):
    monkeypatch.setattr(
        "builtins.open",
        mock_open(
            read_data="# nameserver 192.0.2.9\nnameserver 192.0.2.53\nsearch example.com\nnameserver 2001:db8::53\n"
        ),
    )
    assert main.resolvers() == ["192.0.2.53", "2001:db8::53"]


def test_resolvers_missing_file(monkeypatch):
    monkeypatch.setattr("builtins.open", Mock(side_effect=OSError))
    assert main.resolvers() == []


@pytest.fixture
def dns_socket(monkeypatch):
    sk = MagicMock()
    sk.__enter__.return_value = sk
    factory = Mock(return_value=sk)
    monkeypatch.setattr(main.socket, "socket", factory)
    monkeypatch.setattr(main, "resolvers", lambda: ["192.0.2.53"])
    monkeypatch.setattr(main.random, "randrange", Mock(side_effect=[123, 42]))
    monkeypatch.setattr(main.time, "perf_counter", Mock(side_effect=[10, 10.025]))
    return sk, factory


@pytest.mark.parametrize(
    ("server", "family"),
    [("192.0.2.53", socket.AF_INET), ("2001:db8::53", socket.AF_INET6)],
)
def test_dns_accepts_matching_nxdomain(monkeypatch, dns_socket, server, family):
    sk, factory = dns_socket
    monkeypatch.setattr(main, "resolvers", lambda: [server])
    sk.recvfrom.return_value = (
        struct.pack(">HHHHHH", 42, 0x8183, 1, 0, 0, 0),
        (server, 53),
    )
    assert main.dns_once() == pytest.approx(25)
    factory.assert_called_once_with(family, socket.SOCK_DGRAM)
    sk.settimeout.assert_called_once_with(main.DNS_TIMEOUT)
    packet, destination = sk.sendto.call_args.args
    assert destination == (server, 53)
    assert struct.unpack(">HHHHHH", packet[:12]) == (42, 0x0100, 1, 0, 0, 0)
    assert packet[12:] == b"\x05nw123\x03www\x06google\x03com\x00\x00\x01\x00\x01"
    sk.__exit__.assert_called_once()


def test_dns_ignores_short_and_unrelated_packets(monkeypatch, dns_socket):
    sk, _ = dns_socket
    sk.recvfrom.side_effect = [
        (b"short", ("192.0.2.53", 53)),
        (struct.pack(">HHHHHH", 41, 0, 0, 0, 0, 0), ("192.0.2.53", 53)),
        (struct.pack(">HHHHHH", 42, 0x8183, 1, 0, 0, 0), ("192.0.2.53", 53)),
    ]
    monkeypatch.setattr(
        main.time, "perf_counter", Mock(side_effect=[10, 10.01, 10.02, 10.03])
    )
    assert main.dns_once() == pytest.approx(30)


@pytest.mark.parametrize("error", [TimeoutError(), OSError("network unreachable")])
def test_dns_socket_errors_are_loss(dns_socket, error):
    sk, _ = dns_socket
    sk.recvfrom.side_effect = error
    assert main.dns_once() is None
    sk.__exit__.assert_called_once()


def test_dns_unrelated_packets_exceed_deadline(monkeypatch, dns_socket):
    sk, _ = dns_socket
    sk.recvfrom.return_value = (b"short", ("192.0.2.53", 53))
    monkeypatch.setattr(main.time, "perf_counter", Mock(side_effect=[10, 13]))
    assert main.dns_once() is None


def test_dns_without_resolvers(monkeypatch):
    monkeypatch.setattr(main, "resolvers", lambda: [])
    assert main.dns_once() is None

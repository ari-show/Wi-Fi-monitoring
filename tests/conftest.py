from unittest.mock import Mock

import pytest

import main


@pytest.fixture(autouse=True)
def block_external_io(monkeypatch):
    """モックの設定漏れで実際の通信や待機が発生することを防ぐ。"""
    for owner, name in (
        (main.subprocess, "run"),
        (main.socket, "socket"),
        (main.time, "sleep"),
    ):
        monkeypatch.setattr(
            owner, name, Mock(side_effect=AssertionError(f"Unexpected {name}"))
        )


@pytest.fixture
def make_target():
    def make(kind="gw", samples=(), window=30, key=None):
        target = main.Target(key or kind, kind.upper(), kind, "192.0.2.1", window)
        target.samples.extend(samples)
        return target

    return make

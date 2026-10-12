import csv
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main


@pytest.fixture
def csv_sink(tmp_path, monkeypatch):
    monkeypatch.setattr(main.shutil, "disk_usage", lambda _: SimpleNamespace(free=1000))
    sinks = []

    def make(header=None, max_bytes=10000, keep=3, min_free=100):
        sink = main.RotatingCsv(
            str(tmp_path / "monitor.csv"),
            header or ["time", "gw"],
            max_bytes,
            keep,
            min_free,
        )
        sinks.append(sink)
        return sink

    yield make
    for sink in sinks:
        sink.close()


def read_rows(path):
    with open(path, newline="") as stream:
        return list(csv.reader(stream))


def test_csv_append_preserves_single_header(csv_sink, tmp_path):
    sink = csv_sink()
    sink.write(["first", 12.5])
    sink.close()
    csv_sink().write(["second", 20])
    assert read_rows(tmp_path / "monitor.csv") == [
        ["time", "gw"],
        ["first", "12.5"],
        ["second", "20"],
    ]


@pytest.mark.parametrize("keep", [0, 1, 3])
def test_csv_rotation_keeps_requested_number_of_archives(csv_sink, tmp_path, keep):
    sink = csv_sink(keep=keep)
    sink.write(["0", 10])
    sink.max_bytes = 1
    for i in range(1, 5):
        sink.write([str(i), 10])
    assert read_rows(tmp_path / "monitor.csv") == [["time", "gw"], ["4", "10"]]
    assert len(list(tmp_path.glob("monitor.csv.*"))) == keep
    for generation in range(1, keep + 1):
        assert read_rows(tmp_path / f"monitor.csv.{generation}") == [
            ["time", "gw"],
            [str(4 - generation), "10"],
        ]


def test_csv_changed_header_archives_old_file(csv_sink, tmp_path):
    sink = csv_sink()
    sink.write(["first", 10])
    sink.close()
    csv_sink(header=["time", "dns"]).write(["second", 25])
    assert read_rows(tmp_path / "monitor.csv.1") == [["time", "gw"], ["first", "10"]]
    assert read_rows(tmp_path / "monitor.csv") == [["time", "dns"], ["second", "25"]]


def test_csv_pauses_and_resumes_at_free_space_threshold(
    csv_sink, tmp_path, monkeypatch
):
    disk_usage = Mock(side_effect=[SimpleNamespace(free=99), SimpleNamespace(free=100)])
    monkeypatch.setattr(main.shutil, "disk_usage", disk_usage)
    sink = csv_sink()
    sink.write(["skipped", 10])
    assert sink.paused
    sink.write(["recorded", 20])
    assert not sink.paused
    assert read_rows(tmp_path / "monitor.csv") == [["time", "gw"], ["recorded", "20"]]


@pytest.mark.parametrize("recovered_grade", ["OK", "N/A"])
def test_events_track_peak_grade_values_and_duration(
    monkeypatch, make_target, recovered_grade
):
    monkeypatch.setattr(main.time, "time", Mock(side_effect=[100, 105, 108, 110]))
    monkeypatch.setattr(main.time, "strftime", lambda _: "stamp")
    sink = Mock()
    tracker = main.EventTracker(sink)
    target = make_target()
    tracker.update(
        [target],
        {"gw": {"loss": 5, "avg": 25}},
        {"gw": "WARN"},
        (-75, 54),
        "initial diagnosis",
    )
    tracker.update(
        [target], {"gw": {"loss": 100, "avg": None}}, {"gw": "BAD"}, (-80, 54)
    )
    tracker.update([target], {"gw": {"loss": 5, "avg": 30}}, {"gw": "WARN"}, (-70, 54))
    assert sink.write.call_count == 1
    tracker.update([target], {"gw": None}, {"gw": recovered_grade})
    assert [call.args[0] for call in sink.write.call_args_list] == [
        [
            "stamp",
            "gw",
            "GW",
            "start",
            "WARN",
            "5.0",
            "25.0",
            "",
            -75,
            "initial diagnosis",
        ],
        [
            "stamp",
            "gw",
            "GW",
            "end",
            "BAD",
            "100.0",
            "30.0",
            "10",
            -70,
            "initial diagnosis",
        ],
    ]
    assert tracker.open == {}


def test_events_close_flushes_open_events_only_once(monkeypatch, make_target):
    monkeypatch.setattr(main.time, "time", Mock(side_effect=[100, 103, 104]))
    monkeypatch.setattr(main.time, "strftime", lambda _: "stamp")
    sink = Mock()
    tracker = main.EventTracker(sink)
    targets = [make_target("gw"), make_target("dns")]
    tracker.update(
        targets,
        {"gw": {"loss": 100, "avg": None}, "dns": None},
        {"gw": "BAD", "dns": "N/A"},
    )
    tracker.close(targets)
    tracker.close(targets)
    assert sink.write.call_count == 2
    assert sink.write.call_args_list[0].args[0][6] == ""
    assert sink.write.call_args.args[0][3:9] == ["end", "BAD", "100.0", "0.0", "3", ""]
    assert tracker.open == {}

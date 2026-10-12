from unittest.mock import Mock

import pytest

import main


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        ([], None),
        ([None, None], {"loss": 100, "avg": None, "jit": None, "max": None, "n": 2}),
        ([12], {"loss": 0, "avg": 12, "jit": 0, "max": 12, "n": 1}),
        ([10, None, 30, 20], {"loss": 25, "avg": 20, "jit": 15, "max": 30, "n": 4}),
    ],
)
def test_target_stats(make_target, samples, expected):
    assert make_target(samples=samples).stats() == expected


@pytest.mark.parametrize("kind", ["gw", "dns"])
def test_probe_tracks_lifetime_counts_and_rolling_window(
    monkeypatch, make_target, kind
):
    probe = Mock(side_effect=[10.0, None, 30.0])
    unused = Mock(side_effect=AssertionError("Wrong probe"))
    monkeypatch.setattr(main, "dns_once", probe if kind == "dns" else unused)
    monkeypatch.setattr(main, "ping_once", probe if kind != "dns" else unused)
    monkeypatch.setattr(main.time, "time", Mock(side_effect=[100, 101, 102]))
    target = make_target(kind, window=2)

    for _ in range(3):
        target.probe()

    assert list(target.samples) == [None, 30.0]
    assert (target.total, target.lost, target.last_ts) == (3, 1, 102)
    assert target.stats()["loss"] == 50
    if kind == "dns":
        probe.assert_called_with()
    else:
        probe.assert_called_with(target.host)


@pytest.mark.parametrize("kind", ["gw", "onu", "isp", "ext", "dns"])
@pytest.mark.parametrize("metric", ["loss", "avg", "jit"])
def test_grade_thresholds_are_inclusive(kind, metric):
    warn, bad = main.LIMITS[kind][metric]
    for value, expected in [
        (warn - 0.1, "OK"),
        (warn, "WARN"),
        (bad - 0.1, "WARN"),
        (bad, "BAD"),
    ]:
        stats = {"loss": 0, "avg": 0, "jit": 0, "n": 100}
        stats[metric] = value
        assert main.grade(kind, stats) == expected


def test_grade_missing_samples_and_single_loss(make_target):
    assert main.grade("gw", None) == "N/A"
    assert main.grade("gw", make_target(samples=[None]).stats()) == "OK"
    assert main.grade("gw", make_target(samples=[None, None]).stats()) == "BAD"
    assert main.grade("gw", make_target(samples=[None, 80]).stats()) == "BAD"


@pytest.mark.parametrize("kind", ["gw", "onu", "isp"])
@pytest.mark.parametrize(
    ("external_samples", "expected"),
    [
        ([10, 10], "N/A"),
        ([80, 80], "N/A"),
        ([200, 200], "BAD"),
        ([None, None], "BAD"),
        ([], "BAD"),
    ],
)
def test_evaluate_icmp_silence(make_target, kind, external_samples, expected):
    targets = [make_target(kind, [None, None]), make_target("ext", external_samples)]
    stats, grades = main.evaluate(targets)
    assert stats[kind]["loss"] == 100
    assert grades[kind] == expected


def test_evaluate_uses_any_healthy_external_target_but_keeps_dns_failure(make_target):
    targets = [
        make_target("gw", [None, None]),
        make_target("ext", [None, None], key="ext1"),
        make_target("ext", [10, 10], key="ext2"),
        make_target("dns", [None, None]),
    ]
    _, grades = main.evaluate(targets)
    assert grades == {"gw": "N/A", "ext1": "BAD", "ext2": "OK", "dns": "BAD"}


@pytest.mark.parametrize(
    ("grades", "wifi", "expected"),
    [
        (
            {"gw": "BAD", "ext": "BAD", "dns": "BAD"},
            (-70, 54),
            "【GW】が悪化 (Wi-Fi 電波が弱い: -70 dBm)",
        ),
        (
            {"gw": "WARN", "ext": "OK", "dns": "OK"},
            None,
            "【GW】が悪化 (端末〜ルーター間: Wi-Fi 干渉/距離、またはルーター負荷)",
        ),
        ({"gw": "N/A", "ext": "BAD", "dns": "BAD"}, None, "【EXT】が悪化"),
        (
            {"gw": "OK", "ext": "OK", "dns": "WARN"},
            None,
            "【DNS】名前解決のみ悪化(DNSサーバ変更を検討)",
        ),
        ({"gw": "N/A", "ext": "OK", "dns": "OK"}, None, "異常なし"),
    ],
)
def test_diagnose_prioritizes_first_failing_layer(make_target, grades, wifi, expected):
    targets = [make_target(kind) for kind in ("gw", "ext", "dns")]
    assert main.diagnose(targets, grades, wifi) == expected


def test_render_handles_pending_samples_and_icmp_silence(make_target):
    targets = [
        make_target("gw", [None, None]),
        make_target("ext", [10, 10]),
        make_target("dns"),
    ]
    stats, grades = main.evaluate(targets)
    output = main.render(targets, stats, grades, None, "異常なし", "en0")
    assert "iface=en0" in output
    assert "ICMP非応答" in output
    assert "計測中" in output
    assert "取得不可" in output
    assert "診断: 異常なし" in output

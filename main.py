#!/usr/bin/env python3
"""netwatch: 回線の層ごとに ping / 名前解決を1秒ごとに行い、悪化した層を特定する。

標準ライブラリのみ(Linux / macOS)。使い方:
  uv run main.py [--onu IP] [--isp IP] [--log FILE] [--window N] [--interval S]
"""

import argparse
import csv
import os
import platform
import random
import re
import shutil
import signal
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
from collections import deque

EXTERNAL = ["1.1.1.1", "8.8.8.8"]
DNS_NAME = "www.google.com"
# (WARN, BAD): ロス率(%), 平均遅延(ms), ジッタ(ms)
# ロスは窓内で 2 回以上落ちたときだけ数える(1 回だけのロスは判定・イベントに含めない)
MIN_LOST_COUNT = 2
LIMITS = {
    "gw": {"loss": (5, 10), "avg": (20, 60), "jit": (10, 30)},
    "onu": {"loss": (5, 10), "avg": (25, 70), "jit": (10, 30)},
    "isp": {"loss": (5, 10), "avg": (40, 100), "jit": (15, 40)},
    "ext": {"loss": (5, 10), "avg": (60, 150), "jit": (20, 60)},
    "dns": {"loss": (5, 10), "avg": (100, 300), "jit": (50, 150)},
}
COL = {"OK": "\033[32m", "WARN": "\033[33m", "BAD": "\033[31m", "N/A": "\033[90m"}
RST = "\033[0m"
IS_MAC = platform.system() == "Darwin"


def run(cmd, timeout=5):
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        ).stdout
    except Exception:
        return ""


def ping_once(host, timeout=1):
    """RTT(ms) を返す。失敗は None。"""
    cmd = ["ping", "-c", "1", "-n"]
    cmd += ["-W", str(timeout * 1000 if IS_MAC else timeout)]
    out = run(cmd + [host], timeout + 2)
    m = re.search(r"time[=<]([\d.]+)", out)
    return float(m.group(1)) if m else None


DNS_TIMEOUT = 2.0  # 秒。超えたら「ロス」として扱う
STALE_SEC = 3.0  # これより古い測定値は CSV に書かず空欄にする


def resolvers():
    """/etc/resolv.conf の nameserver 一覧。"""
    try:
        with open("/etc/resolv.conf") as f:
            return [
                m.group(1) for m in re.finditer(r"^nameserver\s+(\S+)", f.read(), re.M)
            ]
    except OSError:
        return []


def dns_once():
    """先頭のリゾルバに存在しない名前を直接問い合わせ、応答時間(ms)を返す。
    NXDOMAIN も応答とみなす。DNS_TIMEOUT 秒で応答がなければ None(ロス)。"""
    servers = resolvers()
    if not servers:
        return None
    labels = f"nw{random.randrange(10**9)}.{DNS_NAME}".split(".")
    qid = random.randrange(65536)
    pkt = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    pkt += b"".join(bytes([len(x)]) + x.encode() for x in labels) + b"\x00"
    pkt += struct.pack(">HH", 1, 1)  # A, IN
    fam = socket.AF_INET6 if ":" in servers[0] else socket.AF_INET
    try:
        with socket.socket(fam, socket.SOCK_DGRAM) as sk:
            sk.settimeout(DNS_TIMEOUT)
            t0 = time.perf_counter()
            sk.sendto(pkt, (servers[0], 53))
            while True:
                data, _ = sk.recvfrom(4096)
                if len(data) >= 12 and struct.unpack(">H", data[:2])[0] == qid:
                    return (time.perf_counter() - t0) * 1000
                if time.perf_counter() - t0 > DNS_TIMEOUT:
                    return None
    except OSError:
        return None


def default_gateway():
    if IS_MAC:
        m = re.search(r"gateway:\s*(\S+)", run(["route", "-n", "get", "default"]))
    else:
        m = re.search(r"default via (\S+)", run(["ip", "route", "show", "default"]))
    return m.group(1) if m else None


def default_iface():
    if IS_MAC:
        m = re.search(r"interface:\s*(\S+)", run(["route", "-n", "get", "default"]))
    else:
        m = re.search(
            r"default via \S+ dev (\S+)", run(["ip", "route", "show", "default"])
        )
    return m.group(1) if m else None


def hop_addrs(dest, max_ttl=4):
    """TTL を増やして ping し、途中ルーターの IP を集める(traceroute 不要)。"""
    hops = []
    for ttl in range(1, max_ttl + 1):
        flag = "-m" if IS_MAC else "-t"
        out = run(
            [
                "ping",
                "-c",
                "1",
                "-n",
                "-W",
                "1000" if IS_MAC else "1",
                flag,
                str(ttl),
                dest,
            ],
            4,
        )
        m = re.search(r"[Ff]rom\s+(\d+\.\d+\.\d+\.\d+)", out) or re.search(
            r"bytes from (\d+\.\d+\.\d+\.\d+)", out
        )
        hops.append(m.group(1) if m else None)
        if m and m.group(1) == dest:
            break
    return hops


def wifi_info(iface):
    """(RSSI dBm, Tx Mbps) を返す。取れなければ None。"""
    if not iface or IS_MAC:
        return None
    out = run(["iw", "dev", iface, "link"])
    sig = re.search(r"signal:\s*(-?\d+)", out)
    tx = re.search(r"tx bitrate:\s*([\d.]+)", out)
    if not sig:
        return None
    return int(sig.group(1)), float(tx.group(1)) if tx else None


class Target:
    def __init__(self, key, label, kind, host, window):
        self.key, self.label, self.kind, self.host = key, label, kind, host
        self.samples = deque(maxlen=window)  # RTT or None
        self.total = self.lost = 0
        self.last_ts = 0.0  # 最後に測定が完了した時刻
        self.lock = threading.Lock()

    def probe(self):
        r = dns_once() if self.kind == "dns" else ping_once(self.host)
        with self.lock:
            self.samples.append(r)
            self.last_ts = time.time()
            self.total += 1
            self.lost += r is None

    def stats(self):
        with self.lock:
            s = list(self.samples)
        if not s:
            return None
        ok = [x for x in s if x is not None]
        loss = 100 * (len(s) - len(ok)) / len(s)
        if not ok:
            return {"loss": loss, "avg": None, "jit": None, "max": None, "n": len(s)}
        jit = (
            statistics.mean(abs(a - b) for a, b in zip(ok, ok[1:]))
            if len(ok) > 1
            else 0.0
        )
        return {
            "loss": loss,
            "avg": statistics.mean(ok),
            "jit": jit,
            "max": max(ok),
            "n": len(s),
        }


def grade(kind, st):
    if st is None:
        return "N/A"
    lim = LIMITS[kind]
    lvl = 0
    for name in ("loss", "avg", "jit"):
        v = st[name]
        if v is None:
            continue
        if name == "loss" and round(v * st["n"] / 100) < MIN_LOST_COUNT:
            continue
        if v >= lim[name][1]:
            lvl = max(lvl, 2)
        elif v >= lim[name][0]:
            lvl = max(lvl, 1)
    return ("OK", "WARN", "BAD")[lvl]


def evaluate(targets):
    """各層の判定。後続の層が正常で自分だけ 100% ロスなら ICMP 非応答(N/A)。"""
    stats = {t.key: t.stats() for t in targets}
    grades = {t.key: grade(t.kind, stats[t.key]) for t in targets}
    ext_ok = any(
        grades[t.key] in ("OK", "WARN") and stats[t.key] and stats[t.key]["loss"] < 100
        for t in targets
        if t.kind == "ext"
    )
    for t in targets:
        st = stats[t.key]
        if t.kind in ("gw", "onu", "isp") and st and st["loss"] >= 100 and ext_ok:
            grades[t.key] = "N/A"
    return stats, grades


def diagnose(targets, grades, wifi):
    names = {t.key: t.label for t in targets}
    order = [t for t in targets if t.kind in ("gw", "onu", "isp", "ext")]
    for t in order:
        if grades[t.key] in ("WARN", "BAD"):
            extra = ""
            if t.kind == "gw" and wifi and wifi[0] <= -70:
                extra = f"(Wi-Fi 電波が弱い: {wifi[0]} dBm)"
            elif t.kind == "gw":
                extra = "(端末〜ルーター間: Wi-Fi 干渉/距離、またはルーター負荷)"
            return f"【{names[t.key]}】が悪化 {extra}".strip()
    if grades.get("dns") in ("WARN", "BAD"):
        return "【DNS】名前解決のみ悪化(DNSサーバ変更を検討)"
    if all(g in ("OK", "N/A") for g in grades.values()):
        return "異常なし"
    return "不明"


def fmt(v, unit=""):
    return "  -  " if v is None else f"{v:5.1f}{unit}"


def render(targets, stats, grades, wifi, diag, iface):
    lines = [
        "\033[H\033[J"
        + time.strftime("netwatch %H:%M:%S")
        + f"  (Ctrl+C で終了)  iface={iface}"
    ]
    lines.append(
        f"{'層':<22}{'宛先':<18}{'状態':<6}{'ロス%':>7}{'平均ms':>8}{'ジッタ':>8}{'最大ms':>8}"
    )
    if wifi:
        tx = f"{wifi[1]:.0f} Mbps" if wifi[1] else "不明"
        lines.append(f"{'①Wi-Fi(端末)':<22}{'':<18}{'':<6}RSSI {wifi[0]} dBm / Tx {tx}")
    else:
        lines.append(f"{'①Wi-Fi(端末)':<22}{'':<18}{'':<6}取得不可(有線 or 非対応)")
    for t in targets:
        st, g = stats[t.key], grades[t.key]
        if st is None:
            row = f"{t.label:<22}{t.host:<18}"
            lines.append(row + "計測中")
            continue
        lines.append(
            f"{t.label:<22}{t.host:<18}{COL[g]}{g:<6}{RST}"
            f"{st['loss']:7.1f}{fmt(st['avg']):>8}{fmt(st['jit']):>8}{fmt(st['max']):>8}"
            + ("  ICMP非応答" if g == "N/A" and t.kind != "dns" else "")
        )
    lines.append("")
    lines.append("診断: " + diag)
    return "\n".join(lines)


class RotatingCsv:
    """サイズ上限で世代交代し、ディスク空きが少ないときは書き込みを止める CSV ログ。"""

    def __init__(self, path, header, max_bytes, keep, min_free):
        self.path, self.header = path, header
        self.max_bytes, self.keep, self.min_free = max_bytes, keep, min_free
        self.paused = False
        self.f = None
        self._open()

    def _open(self):
        if os.path.exists(self.path):
            with open(self.path, newline="") as f:
                first = f.readline().rstrip("\r\n")
            if first != ",".join(self.header):  # 列構成が変わっていたら別ファイルに退避
                self._rotate()
                return
        self.f = open(self.path, "a", newline="")
        self.w = csv.writer(self.f, lineterminator="\n")
        if self.f.tell() == 0:
            self.w.writerow(self.header)

    def _rotate(self):
        if self.f:
            self.f.close()
        for i in range(self.keep, 0, -1):
            src = self.path if i == 1 else f"{self.path}.{i - 1}"
            if os.path.exists(src):
                os.replace(src, f"{self.path}.{i}")
        self.f = open(self.path, "w", newline="")
        self.w = csv.writer(self.f, lineterminator="\n")
        self.w.writerow(self.header)

    def write(self, row):
        free = shutil.disk_usage(os.path.dirname(os.path.abspath(self.path))).free
        self.paused = free < self.min_free
        if self.paused:
            return
        if self.f.tell() >= self.max_bytes:
            self._rotate()
        self.w.writerow(row)
        self.f.flush()

    def close(self):
        if self.f:
            self.f.close()


class EventTracker:
    """WARN/BAD になった区間を開始・終了として events ログに残す。"""

    def __init__(self, sink):
        self.sink, self.open = sink, {}

    def update(self, targets, stats, grades, wifi=None, diag=""):
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        rssi = "" if not wifi else wifi[0]
        for t in targets:
            g, st = grades[t.key], stats[t.key]
            if g in ("WARN", "BAD"):
                ev = self.open.get(t.key)
                if ev is None:
                    ev = self.open[t.key] = {
                        "t0": now,
                        "g": g,
                        "loss": 0.0,
                        "avg": 0.0,
                        "rssi": rssi,
                        "diag": diag,
                    }
                    self.sink.write(
                        [
                            stamp,
                            t.key,
                            t.label,
                            "start",
                            g,
                            f"{st['loss']:.1f}",
                            "" if st["avg"] is None else f"{st['avg']:.1f}",
                            "",
                            rssi,
                            diag,
                        ]
                    )
                if g == "BAD":
                    ev["g"] = "BAD"
                ev["rssi"] = rssi
                ev["loss"] = max(ev["loss"], st["loss"])
                ev["avg"] = max(ev["avg"], st["avg"] or 0.0)
            elif t.key in self.open:
                self._end(t, stamp, now)

    def _end(self, t, stamp, now):
        ev = self.open.pop(t.key)
        self.sink.write(
            [
                stamp,
                t.key,
                t.label,
                "end",
                ev["g"],
                f"{ev['loss']:.1f}",
                f"{ev['avg']:.1f}",
                f"{now - ev['t0']:.0f}",
                ev["rssi"],
                ev["diag"],
            ]
        )

    def close(self, targets):
        stamp, now = time.strftime("%Y-%m-%d %H:%M:%S"), time.time()
        for t in targets:
            if t.key in self.open:
                self._end(t, stamp, now)


def main():
    ap = argparse.ArgumentParser(description="回線の層別 ping 監視")
    ap.add_argument("--onu", help="ONU/モデムの IP(手動指定)")
    ap.add_argument("--isp", help="ISP 初段の IP(手動指定)")
    ap.add_argument("--gw", help="ルーターの IP(手動指定)")
    ap.add_argument(
        "--log", help="CSV ログの出力先(悪化イベントは <名前>.events.csv にも記録)"
    )
    ap.add_argument(
        "--log-max-mb",
        type=float,
        default=300,
        help="CSV 1ファイルの安全上限MB(1か月で約130MB。超えたら世代交代)",
    )
    ap.add_argument(
        "--log-keep", type=int, default=3, help="残す古い世代の数(.1 .2 ...)"
    )
    ap.add_argument(
        "--min-free-mb",
        type=float,
        default=500,
        help="ディスク空きがこれ未満なら記録を止める",
    )
    ap.add_argument("--window", type=int, default=30, help="集計する直近の回数")
    ap.add_argument(
        "--quiet", action="store_true", help="画面表示をしない(サービス常駐用)"
    )
    ap.add_argument("--interval", type=float, default=1.0, help="間隔(秒)")
    a = ap.parse_args()

    gw = a.gw or default_gateway()
    for _ in range(60):  # 起動直後でネットワーク未接続なら最大2分待つ
        if gw:
            break
        time.sleep(2)
        gw = default_gateway()
    iface = default_iface()
    print("経路を検出中...")
    hops = hop_addrs(EXTERNAL[0])
    # hops[0] は通常 GW。その先の最初の2つを ONU / ISP とする。
    gw = gw or (hops[0] if hops else None)
    gw_at_start = default_gateway()
    rest = [h for h in hops if h and h != gw and h != EXTERNAL[0]]
    onu = a.onu or (rest[0] if len(rest) > 0 else None)
    isp = a.isp or (rest[1] if len(rest) > 1 else None)
    if onu and onu == isp:
        isp = None

    targets = []
    if gw:
        targets.append(Target("gw", "②ルーター(GW)", "gw", gw, a.window))
    if onu:
        targets.append(Target("onu", "③ONU/モデム", "onu", onu, a.window))
    if isp:
        targets.append(Target("isp", "④ISP初段", "isp", isp, a.window))
    for h in EXTERNAL:
        targets.append(Target(f"ext_{h}", f"⑤外部({h})", "ext", h, a.window))
    rs = resolvers()
    targets.append(
        Target("dns", "DNS(名前解決)", "dns", rs[0] if rs else "なし", a.window)
    )
    if not onu or not isp:
        print(
            "注: ③/④ を自動検出できませんでした。必要なら --onu / --isp で指定してください。"
        )

    sink = events = None
    if a.log:
        mb = 1024 * 1024
        sink = RotatingCsv(
            a.log,
            ["time"] + [t.key for t in targets],
            int(a.log_max_mb * mb),
            a.log_keep,
            int(a.min_free_mb * mb),
        )
        base, _ = os.path.splitext(a.log)
        ev_sink = RotatingCsv(
            base + ".events.csv",
            [
                "time",
                "target",
                "label",
                "event",
                "grade",
                "max_loss",
                "max_avg_ms",
                "duration_s",
                "rssi_dbm",
                "diag",
            ],
            5 * mb,
            1,
            int(a.min_free_mb * mb),
        )
        events = EventTracker(ev_sink)

    stop = threading.Event()

    def on_term(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    restart = False
    ticks = 0

    def loop(t):
        while not stop.is_set():
            t0 = time.time()
            t.probe()
            stop.wait(max(0, a.interval - (time.time() - t0)))

    threads = [threading.Thread(target=loop, args=(t,), daemon=True) for t in targets]
    for th in threads:
        th.start()
    try:
        while True:
            time.sleep(a.interval)
            stats, grades = evaluate(targets)
            wifi = wifi_info(iface)
            diag = diagnose(targets, grades, wifi)
            if not a.quiet:
                sys.stdout.write(
                    render(targets, stats, grades, wifi, diag, iface) + "\n"
                )
                sys.stdout.flush()
            if events:
                events.update(targets, stats, grades, wifi, diag)
            if sink:
                row = [time.strftime("%Y-%m-%d %H:%M:%S")]
                for t in targets:
                    with t.lock:
                        last = t.samples[-1] if t.samples else None
                        stale = time.time() - t.last_ts > STALE_SEC
                    row.append("" if last is None or stale else f"{last:.1f}")
                sink.write(row)
                if sink.paused and not a.quiet:
                    sys.stdout.write(
                        "\033[33m注意: ディスク空きが少ないためログ記録を停止中\033[0m\n"
                    )
            ticks += 1
            if (
                ticks % 300 == 0 and not a.gw
            ):  # 5分ごと: ルーターが変わったら検出し直すため終了
                now_gw = default_gateway()
                if now_gw and gw_at_start and now_gw != gw_at_start:
                    print(
                        f"ゲートウェイが {gw_at_start} → {now_gw} に変わったため再起動します"
                    )
                    restart = True
                    break
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if events:
            events.close(targets)
        print("\n終了しました。")
    if restart:
        sys.exit(3)  # systemd の Restart=always で再検出つきで起動し直す


if __name__ == "__main__":
    main()

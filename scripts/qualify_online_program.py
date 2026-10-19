#!/usr/bin/env python3
"""Combined sustained gate for Core, YouTube delivery, and host headroom."""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import time
from typing import Any, Callable


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = load_module("mxl_online_demo", ROOT / "deploy" / "online-demo" / "mxl_demo.py")
youtube_gate = load_module("mxl_youtube_gate", ROOT / "scripts" / "check_youtube_demo.py")


def parse_proc_stat(text: str) -> tuple[int, int]:
    fields = text.splitlines()[0].split()
    if not fields or fields[0] != "cpu" or len(fields) < 5:
        raise ValueError("/proc/stat did not contain aggregate CPU counters")
    values = [int(value) for value in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def parse_mem_available_mib(text: str) -> float:
    for line in text.splitlines():
        fields = line.split()
        if fields[:1] == ["MemAvailable:"] and len(fields) >= 2:
            return int(fields[1]) / 1024
    raise ValueError("/proc/meminfo did not contain MemAvailable")


def host_sample() -> dict[str, Any]:
    return {
        "cpu": parse_proc_stat(pathlib.Path("/proc/stat").read_text(encoding="utf-8")),
        "mem_available_mib": parse_mem_available_mib(
            pathlib.Path("/proc/meminfo").read_text(encoding="utf-8")
        ),
    }


def host_summary(samples: list[dict[str, Any]], minimum_idle_percent: float) -> dict[str, Any]:
    idle_percentages = []
    for previous, current in zip(samples, samples[1:]):
        total_delta = current["cpu"][0] - previous["cpu"][0]
        idle_delta = current["cpu"][1] - previous["cpu"][1]
        if total_delta > 0:
            idle_percentages.append(100 * idle_delta / total_delta)
    memory = [float(sample["mem_available_mib"]) for sample in samples]
    minimum_idle = min(idle_percentages) if idle_percentages else 0.0
    return {
        "passed": bool(idle_percentages) and minimum_idle >= minimum_idle_percent,
        "samples": len(samples),
        "minimum_idle_percent_required": minimum_idle_percent,
        "idle_percent_min": round(minimum_idle, 3),
        "idle_percent_max": round(max(idle_percentages), 3) if idle_percentages else 0.0,
        "available_memory_mib_min": round(min(memory), 3) if memory else 0.0,
        "available_memory_mib_max": round(max(memory), 3) if memory else 0.0,
    }


def summarize_multiview_viewer(samples: list[dict[str, Any]]) -> dict[str, Any]:
    matched: list[list[dict[str, Any]]] = []
    for sample in samples:
        items = sample.get("items", [])
        if not isinstance(items, list):
            return {"passed": False, "reason": "MediaMTX WebRTC response had no items list"}
        matched.append([
            item for item in items
            if isinstance(item, dict)
            and str(item.get("path", "")).lstrip("/") == "mxl-multiview"
            and item.get("state") == "read"
            and bool(item.get("peerConnectionEstablished"))
        ])
    if not matched:
        return {"passed": False, "reason": "no MediaMTX samples"}
    stable_ids = set(str(item.get("id")) for item in matched[0])
    for group in matched[1:]:
        stable_ids.intersection_update(str(item.get("id")) for item in group)
    byte_totals = []
    packet_totals = []
    discarded = []
    for group in matched:
        stable = [item for item in group if str(item.get("id")) in stable_ids]
        byte_totals.append(sum(int(item.get("outboundBytes", item.get("bytesSent", 0))) for item in stable))
        packet_totals.append(sum(int(item.get("outboundRTPPackets", item.get("rtpPacketsSent", 0))) for item in stable))
        discarded.extend(int(item.get("outboundFramesDiscarded", 0)) for item in stable)
    byte_deltas = [current - previous for previous, current in zip(byte_totals, byte_totals[1:])]
    packet_deltas = [current - previous for previous, current in zip(packet_totals, packet_totals[1:])]
    viewer_counts = [len(group) for group in matched]
    passed = bool(
        stable_ids
        and len(samples) >= 2
        and min(viewer_counts) >= 1
        and byte_deltas
        and min(byte_deltas) > 0
        and packet_deltas
        and min(packet_deltas) > 0
        and (not discarded or max(discarded) == 0)
    )
    return {
        "passed": passed,
        "samples": len(samples),
        "viewer_count_min": min(viewer_counts),
        "viewer_count_max": max(viewer_counts),
        "stable_viewers": len(stable_ids),
        "outbound_bytes_delta_min": min(byte_deltas) if byte_deltas else 0,
        "outbound_rtp_packets_delta_min": min(packet_deltas) if packet_deltas else 0,
        "outbound_frames_discarded_max": max(discarded) if discarded else 0,
        **({"reason": "multiview WebRTC reader was absent, replaced, stalled, or discarding frames"}
           if not passed else {}),
    }


def write_private_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def run_gate(
    cfg: dict[str, Any],
    *,
    controller_url: str,
    seconds: float,
    interval: float,
    minimum_idle_percent: float,
    fetch: Callable[..., dict[str, Any]] = youtube_gate.fetch_state,
    fetch_webrtc: Callable[..., dict[str, Any]] = youtube_gate.fetch_state,
    sample_host: Callable[[], dict[str, Any]] = host_sample,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    state_endpoint = youtube_gate.state_url(controller_url)
    webrtc_endpoint = f"http://127.0.0.1:{int(cfg['mediamtx_api_port'])}/v3/webrtc/sessions/list"
    started_monotonic = clock()
    started_utc = datetime.datetime.now(datetime.timezone.utc)
    deadline = started_monotonic + seconds
    before = demo.acceptance_snapshot(cfg)
    youtube_samples: list[dict[str, Any]] = []
    webrtc_samples: list[dict[str, Any]] = []
    host_samples: list[dict[str, Any]] = []
    read_error = None
    while True:
        try:
            youtube_samples.append(fetch(state_endpoint))
            webrtc_samples.append(fetch_webrtc(webrtc_endpoint))
            host_samples.append(sample_host())
        except (OSError, ValueError, json.JSONDecodeError) as error:
            read_error = str(error)
            break
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleeper(min(interval, remaining))
    after = demo.acceptance_snapshot(cfg)
    elapsed = clock() - started_monotonic
    youtube = youtube_gate.summarize(youtube_samples, int(cfg["source_count"]), elapsed)
    if read_error:
        youtube["passed"] = False
        youtube["reason"] = f"monitor read failed: {read_error}"
    core = demo.evaluate_acceptance(before, after, int(cfg["source_count"]), cfg["deployment_id"])
    resources = host_summary(host_samples, minimum_idle_percent)
    multiview_viewer = summarize_multiview_viewer(webrtc_samples)
    completed_utc = datetime.datetime.now(datetime.timezone.utc)
    return {
        "schema": "mxl-online-program-qualification-v1",
        "passed": bool(
            core["passed"] and youtube["passed"] and resources["passed"]
            and multiview_viewer["passed"]
        ),
        "requested_duration_s": seconds,
        "elapsed_seconds": round(elapsed, 3),
        "sample_interval_s": interval,
        "started_at_utc": started_utc.isoformat(),
        "completed_at_utc": completed_utc.isoformat(),
        "core": core,
        "youtube": youtube,
        "multiview_webrtc_viewer": multiview_viewer,
        "host_resources": resources,
        "human_observations_required": [
            "audience picture visibly advances at the LIVE edge",
            "diagnostic timecode differs between two captures at least ten seconds apart",
            "all intended Program sources are audible without artifacts",
            "audience A/V offset is acceptable throughout the operated window",
        ],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=pathlib.Path, default=pathlib.Path("demo-config.json"))
    result.add_argument("--url", default="http://127.0.0.1:18123")
    result.add_argument("--seconds", type=float, default=1800.0)
    result.add_argument("--interval", type=float, default=2.0)
    result.add_argument("--minimum-idle-percent", type=float, default=20.0)
    result.add_argument("--output", type=pathlib.Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.seconds < 30:
        raise SystemExit("seconds must be at least 30")
    if args.interval <= 0 or args.interval > args.seconds:
        raise SystemExit("interval must be positive and no greater than seconds")
    if not 0 <= args.minimum_idle_percent <= 100:
        raise SystemExit("minimum idle percent must be between 0 and 100")
    cfg = demo.load_config(args.config.resolve())
    output = args.output or pathlib.Path(cfg["state_dir"]) / "program-qualification-latest.json"
    report = run_gate(
        cfg,
        controller_url=args.url,
        seconds=args.seconds,
        interval=args.interval,
        minimum_idle_percent=args.minimum_idle_percent,
    )
    write_private_json(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"{'PASS' if report['passed'] else 'FAIL'} combined qualification: {output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

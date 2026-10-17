#!/usr/bin/env python3
"""Read-only sustained readiness gate for the loopback YouTube demo controller."""

from __future__ import annotations

import argparse
import datetime
import json
import time
import urllib.parse
import urllib.request
from typing import Any, Callable


DEFAULT_RSS_WARMUP_SECONDS = 300.0
DEFAULT_MAX_RSS_GROWTH_MIB_PER_HOUR = 128.0


def state_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("controller URL must be loopback HTTP through the SSH tunnel")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("controller URL cannot contain credentials, query, or fragment")
    return base_url.rstrip("/") + "/api/state"


def fetch_state(url: str, opener: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener(request, timeout=5.0) as response:
        return json.loads(response.read())


def summarize(
    samples: list[dict[str, Any]],
    required_sources: int,
    elapsed_seconds: float | None = None,
    rss_warmup_seconds: float = DEFAULT_RSS_WARMUP_SECONDS,
    max_rss_growth_mib_per_hour: float = DEFAULT_MAX_RSS_GROWTH_MIB_PER_HOUR,
) -> dict[str, Any]:
    if not samples:
        return {"passed": False, "reason": "no samples"}
    outputs = [sample.get("output") for sample in samples]
    if any(not isinstance(output, dict) for output in outputs):
        return {"passed": False, "reason": "output was not running for every sample"}
    typed_outputs: list[dict[str, Any]] = outputs  # type: ignore[assignment]
    failed = sorted({
        str(check)
        for output in typed_outputs
        for check in output.get("failed_checks", [])
    })
    metrics = [output.get("metrics", {}) for output in typed_outputs]
    source_rates = [
        float(rate)
        for metric in metrics
        for rate in metric.get("source_fps", {}).values()
    ]
    rss_points = [
        (
            float(sample["_sample_elapsed_seconds"]),
            float(output.get("metrics", {}).get("process_rss_mib", 0.0)),
        )
        for sample, output in zip(samples, typed_outputs)
        if float(sample.get("_sample_elapsed_seconds", -1.0)) >= rss_warmup_seconds
    ]
    rss_slope_applied = (
        len(rss_points) >= 2 and rss_points[-1][0] - rss_points[0][0] >= 60.0
    )
    rss_growth_mib_per_hour = None
    if rss_slope_applied:
        mean_time = sum(point[0] for point in rss_points) / len(rss_points)
        mean_rss = sum(point[1] for point in rss_points) / len(rss_points)
        denominator = sum((point[0] - mean_time) ** 2 for point in rss_points)
        slope_mib_per_second = (
            sum(
                (point[0] - mean_time) * (point[1] - mean_rss)
                for point in rss_points
            ) / denominator
            if denominator
            else 0.0
        )
        rss_growth_mib_per_hour = max(0.0, slope_mib_per_second * 3600.0)
    transport_passed = (
            all(bool(output.get("ready")) for output in typed_outputs)
            and all(int(sample.get("source_count", 0)) >= required_sources for sample in samples)
            and all(int(output.get("reconnects", 0)) == 0 for output in typed_outputs)
    )
    rss_passed = (
        not rss_slope_applied
        or rss_growth_mib_per_hour is not None
        and rss_growth_mib_per_hour <= max_rss_growth_mib_per_hour
    )
    if not rss_passed:
        failed.append("process_rss_growth")
    result = {
        "passed": transport_passed and rss_passed,
        "samples": len(samples),
        "ready_samples": sum(bool(output.get("ready")) for output in typed_outputs),
        "required_sources": required_sources,
        "failed_checks": failed,
        "composed_fps_min": round(min(float(metric.get("composed_fps", 0.0)) for metric in metrics), 3),
        "video_fps_min": round(min(float(metric.get("video_fps", 0.0)) for metric in metrics), 3),
        "mux_mbps_min": round(min(float(metric.get("mux_kbps", 0.0)) for metric in metrics) / 1000, 3),
        "sent_mbps_min": round(min(float(metric.get("sent_kbps", 0.0)) for metric in metrics) / 1000, 3),
        "clock_fps_min": round(min(float(metric.get("timecode_fps", 0.0)) for metric in metrics), 3),
        "audio_buffers_per_second_min": round(min(float(metric.get("audio_buffers_per_second", 0.0)) for metric in metrics), 3),
        "audio_buffers_per_second_max": round(max(float(metric.get("audio_buffers_per_second", 0.0)) for metric in metrics), 3),
        "source_fps_min": round(min(source_rates), 3) if source_rates else None,
        "source_lateness_ms_max": round(max(float(metric.get("source_lateness_max_ms", 0.0)) for metric in metrics), 3),
        "send_queue_level_ms_max": round(max(float(metric.get("send_queue_level_ms") or 0.0) for metric in metrics), 3),
        "reconnects_max": max(int(output.get("reconnects", 0)) for output in typed_outputs),
        "process_cpu_cores_min": round(min(float(metric.get("process_cpu_cores", 0.0)) for metric in metrics), 3),
        "process_cpu_cores_max": round(max(float(metric.get("process_cpu_cores", 0.0)) for metric in metrics), 3),
        "process_rss_mib_min": round(min(float(metric.get("process_rss_mib", 0.0)) for metric in metrics), 3),
        "process_rss_mib_max": round(max(float(metric.get("process_rss_mib", 0.0)) for metric in metrics), 3),
        "rss_slope_check_applied": rss_slope_applied,
        "rss_warmup_seconds": rss_warmup_seconds,
        "max_rss_growth_mib_per_hour": max_rss_growth_mib_per_hour,
        "rss_growth_mib_per_hour": (
            None if rss_growth_mib_per_hour is None else round(rss_growth_mib_per_hour, 3)
        ),
        "rss_projected_8h_growth_mib": (
            None if rss_growth_mib_per_hour is None
            else round(rss_growth_mib_per_hour * 8.0, 3)
        ),
    }
    if elapsed_seconds is not None:
        result["elapsed_seconds"] = round(elapsed_seconds, 3)
    if not result["passed"]:
        result["reason"] = "one or more sustained demo gates failed"
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--url", default="http://127.0.0.1:18121")
    result.add_argument("--seconds", type=float, default=30.0)
    result.add_argument("--interval", type=float, default=2.0)
    result.add_argument("--required-sources", type=int, choices=(4, 8), default=8)
    result.add_argument(
        "--rss-warmup-seconds", type=float, default=DEFAULT_RSS_WARMUP_SECONDS,
        help="ignore normal startup allocation before measuring RSS growth",
    )
    result.add_argument(
        "--max-rss-growth-mib-per-hour", type=float,
        default=DEFAULT_MAX_RSS_GROWTH_MIB_PER_HOUR,
        help="fail long gates when the post-warmup RSS trend exceeds this rate",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if (
        args.seconds <= 0 or args.interval <= 0
        or args.rss_warmup_seconds < 0
        or args.max_rss_growth_mib_per_hour <= 0
    ):
        raise SystemExit(
            "seconds, interval, and RSS limit must be positive; RSS warmup cannot be negative"
        )
    try:
        url = state_url(args.url)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    started_monotonic = time.monotonic()
    started_utc = datetime.datetime.now(datetime.timezone.utc)
    deadline = started_monotonic + args.seconds
    samples: list[dict[str, Any]] = []
    while True:
        try:
            sample = fetch_state(url)
            sample["_sample_elapsed_seconds"] = time.monotonic() - started_monotonic
            samples.append(sample)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(json.dumps({"passed": False, "reason": f"controller read failed: {error}"}))
            return 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(args.interval, remaining))
    completed_utc = datetime.datetime.now(datetime.timezone.utc)
    result = summarize(
        samples,
        args.required_sources,
        time.monotonic() - started_monotonic,
        args.rss_warmup_seconds,
        args.max_rss_growth_mib_per_hour,
    )
    result["started_at_utc"] = started_utc.isoformat()
    result["completed_at_utc"] = completed_utc.isoformat()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

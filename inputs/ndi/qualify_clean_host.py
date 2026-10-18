#!/usr/bin/env python3
"""Guarded two-source clean-host qualification for the native NDI bank.

The operator supplies private source and Core configuration files.  This tool
never prints or copies their contents.  It owns one bank container, one
isolated Core container, and one new MXL domain.  A run is a dry run unless
``--apply`` is present.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from inputs.ndi.ndi_bank import HISTORY_OPTION, load_sources


OWNER_LABEL = "io.mxl.qualifier.owner"
ROLE_LABEL = "io.mxl.qualifier.role"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,47}$")
HARD_COUNTERS = (
    "invalid", "too_early", "too_late", "timeouts", "reader_reopens",
    "sync_timeouts", "sync_too_early", "sync_too_late", "no_common_window",
    "selected_input_regressions", "sustained_unavailable_alarms",
    "selected_input_skip_events", "selected_input_skipped_grains",
)


class QualificationError(RuntimeError):
    """A fail-closed preflight, runtime, or acceptance error."""


Run = Callable[..., subprocess.CompletedProcess[str]]
Fetch = Callable[[str], dict[str, Any]]
CpuReader = Callable[[], tuple[int, int]]


def command_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, **kwargs)


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=3) as response:
        if response.status != 200:
            raise QualificationError(f"health endpoint returned HTTP {response.status}")
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise QualificationError("health endpoint did not return an object")
    return value


def loopback_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value.rstrip("/"))
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.port is None):
        raise QualificationError("health URLs must use loopback HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise QualificationError("health URLs must not contain credentials, query, or fragment")
    return value.rstrip("/")


def port_free(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    family = socket.AF_INET6 if parsed.hostname == "::1" else socket.AF_INET
    host = parsed.hostname if parsed.hostname != "localhost" else "127.0.0.1"
    with socket.socket(family, socket.SOCK_STREAM) as candidate:
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            candidate.bind((host, parsed.port))
            return True
        except OSError:
            return False


def read_host_cpu(path: pathlib.Path = pathlib.Path("/proc/stat")) -> tuple[int, int]:
    """Return cumulative idle and total CPU ticks; steal remains busy time."""
    fields = path.read_text(encoding="utf-8").splitlines()[0].split()
    if not fields or fields[0] != "cpu" or len(fields) < 9:
        raise QualificationError("host CPU accounting is unavailable")
    values = [int(value) for value in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    # Linux guest and guest_nice are already included in user and nice.
    # Summing them again would understate host idle time.
    return idle, sum(values[:8])


def _checked(result: subprocess.CompletedProcess[str], purpose: str) -> str:
    if result.returncode:
        # Docker errors can echo private bind-mount paths.  Keep the public
        # failure aggregate and leave detailed diagnosis to a direct operator
        # invocation of the rejected command.
        raise QualificationError(f"{purpose} failed with exit {result.returncode}")
    return result.stdout.strip()


class Docker:
    """Small injectable Docker boundary.  No method uses a shell."""

    def __init__(self, run: Run = command_run) -> None:
        self.run = run

    def read(self, *args: str) -> str:
        return _checked(self.run(["docker", *args]), "docker read")

    def mutate(self, *args: str) -> str:
        return _checked(self.run(["docker", *args]), "docker mutation")

    def image_id(self, image: str) -> str:
        value = self.read("image", "inspect", "--format", "{{.Id}}", image)
        if not value.startswith("sha256:"):
            raise QualificationError("Docker returned a non-content-addressed image ID")
        return value

    def inspect_optional(self, name: str) -> dict[str, Any] | None:
        result = self.run(["docker", "inspect", name])
        if result.returncode:
            return None
        value = json.loads(result.stdout)
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            raise QualificationError("malformed Docker inspect response")
        return value[0]

    def running_ids(self) -> set[str]:
        raw = self.read("ps", "--quiet", "--no-trunc")
        return {line.strip() for line in raw.splitlines() if line.strip()}

    def logs(self, name: str) -> str:
        result = self.run(["docker", "logs", name])
        if result.returncode:
            raise QualificationError("unable to read candidate timing telemetry")
        return result.stdout + result.stderr

    def stats(self, names: list[str]) -> dict[str, tuple[float, float]]:
        raw = self.read("stats", "--no-stream", "--format", "{{json .}}", *names)
        answer: dict[str, tuple[float, float]] = {}
        for line in raw.splitlines():
            item = json.loads(line)
            answer[item["Name"]] = (
                float(str(item["CPUPerc"]).rstrip("%")),
                parse_bytes(str(item["MemUsage"]).split("/")[0].strip()),
            )
        return answer


def parse_bytes(value: str) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)", value)
    if not match:
        raise QualificationError("unrecognized Docker memory value")
    number, unit = float(match.group(1)), match.group(2)
    factors = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3,
               "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    return number * factors[unit]


@dataclass(frozen=True)
class Settings:
    deployment_id: str
    bank_image: str
    core_image: str
    source_config: pathlib.Path
    core_config: pathlib.Path
    definitions: pathlib.Path
    domain_root: pathlib.Path
    evidence_dir: pathlib.Path
    bank_url: str
    core_url: str
    smoke_seconds: int = 60
    gate_seconds: int = 180
    interval_seconds: int = 10
    max_hold_ratio: float = 0.05
    min_fps: float = 28.5
    max_fps: float = 30.5
    max_bank_cpu_percent: float = 300.0
    max_bank_rss_bytes: float = 500 * 1024 * 1024
    max_core_cpu_percent: float = 200.0
    max_core_rss_bytes: float = 512 * 1024 * 1024
    min_host_idle_percent: float = 20.0
    max_rss_growth_bytes: float = 64 * 1024 * 1024
    max_rss_growth_ratio: float = 0.10

    @property
    def bank_name(self) -> str: return f"{self.deployment_id}-ndi-bank"

    @property
    def core_name(self) -> str: return f"{self.deployment_id}-core"

    @property
    def domain(self) -> pathlib.Path: return self.domain_root / self.deployment_id


def parse_core_config(path: pathlib.Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}; source_flows: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"): continue
        if "=" not in line:
            raise QualificationError("Core config contains a malformed line")
        key, value = (part.strip() for part in line.split("=", 1))
        if key == "source":
            parts = value.split("|")
            if len(parts) != 3 or not all(parts):
                raise QualificationError("Core config contains a malformed source")
            source_flows.append(parts[1]); continue
        if key in values:
            raise QualificationError("Core config contains a duplicate setting")
        values[key] = value
    return values, source_flows


def _paths_overlap(left: pathlib.Path, right: pathlib.Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def validate_settings(settings: Settings) -> list[str]:
    if not NAME_RE.fullmatch(settings.deployment_id):
        raise QualificationError("deployment ID must be 3-48 lowercase safe characters")
    if (settings.smoke_seconds, settings.gate_seconds) != (60, 180):
        raise QualificationError("clean-host v1 stages are fixed at 60 then 180 seconds")
    if settings.interval_seconds <= 0:
        raise QualificationError("sample interval must be positive")
    if settings.interval_seconds > min(settings.smoke_seconds, settings.gate_seconds):
        raise QualificationError("sample interval cannot exceed a stage duration")
    if not 0 <= settings.max_hold_ratio <= 1 or settings.min_fps <= 0 or settings.max_fps < settings.min_fps:
        raise QualificationError("invalid acceptance thresholds")
    for path, kind in ((settings.source_config, "file"), (settings.core_config, "file"),
                       (settings.definitions, "directory"), (settings.domain_root, "directory")):
        if (kind == "file" and not path.is_file()) or (kind == "directory" and not path.is_dir()):
            raise QualificationError(f"required private {kind} is unavailable")
    if settings.domain.exists():
        raise QualificationError("qualification domain already exists")
    if settings.evidence_dir.exists():
        raise QualificationError("evidence directory already exists")
    loopback_url(settings.bank_url); loopback_url(settings.core_url)
    bank_parsed, core_parsed = (urllib.parse.urlparse(settings.bank_url),
                                urllib.parse.urlparse(settings.core_url))
    if bank_parsed.path not in {"", "/"}:
        raise QualificationError("bank health URL must not contain a path")
    if core_parsed.path.rstrip("/") != "/api/mxl/v1":
        raise QualificationError("Core URL must end at /api/mxl/v1")
    if core_parsed.hostname != "127.0.0.1":
        raise QualificationError("Core URL must use 127.0.0.1")
    if bank_parsed.port == core_parsed.port:
        raise QualificationError("bank and Core health ports must be distinct")
    if settings.domain_root.is_symlink():
        raise QualificationError("domain root must not be a symbolic link")
    root = settings.domain_root.resolve()
    domain = settings.domain.resolve(strict=False)
    evidence = settings.evidence_dir.resolve(strict=False)
    if evidence == root or root in evidence.parents or _paths_overlap(domain, evidence):
        raise QualificationError("evidence and MXL domain paths must be disjoint")
    if shutil.disk_usage(settings.domain_root).free < 2 * 1024**3:
        raise QualificationError("domain root requires at least 2 GiB free")
    sources = load_sources(settings.source_config)
    if len(sources) != 2:
        raise QualificationError("clean-host v1 requires exactly two NDI sources")
    if any(source.get("audio_mode") != "disabled" for source in sources):
        raise QualificationError("clean-host v1 is native video-only")
    bank_flows = [source["video_flow"] for source in sources]
    core, core_flows = parse_core_config(settings.core_config)
    if core.get("domain") != "/mxl-domain":
        raise QualificationError("Core config domain must be /mxl-domain")
    if core.get("control_host") != core_parsed.hostname:
        raise QualificationError("Core config control host must match the Core URL")
    if core.get("control_port") != str(core_parsed.port):
        raise QualificationError("Core config control port must match the Core URL")
    if len(core_flows) != 2 or len(set(core_flows)) != 2 or set(core_flows) != set(bank_flows):
        raise QualificationError("Core config must contain exactly the two bank video flows")
    return bank_flows


def _counter_delta(before: dict[str, Any], after: dict[str, Any], key: str) -> int:
    left, right = before.get(key), after.get(key)
    if isinstance(left, bool) or isinstance(right, bool) or not isinstance(left, int) or not isinstance(right, int):
        raise QualificationError(f"Core health is missing integer counter {key}")
    if right < left:
        raise QualificationError(f"Core counter {key} regressed")
    return right - left


def _heads(health: dict[str, Any], flow_ids: list[str]) -> list[int]:
    entries = health.get("sources")
    if not isinstance(entries, list):
        raise QualificationError("Core health does not expose two source heads")
    by_flow = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("flow_id") in flow_ids:
            if entry["flow_id"] in by_flow:
                raise QualificationError("Core health repeats a qualified source")
            by_flow[entry["flow_id"]] = entry.get("latest_head_index")
    heads = [by_flow.get(flow_id) for flow_id in flow_ids]
    if len(heads) != 2 or any(isinstance(value, bool) or not isinstance(value, int) for value in heads):
        raise QualificationError("Core health source heads are unavailable")
    return heads


def timing_events(logs: str) -> list[dict[str, Any]]:
    answer = []
    for line in logs.splitlines():
        if "ndi_bank_timing" not in line:
            continue
        try:
            value = json.loads(line[line.index("{"):])
        except (ValueError, json.JSONDecodeError):
            raise QualificationError("malformed NDI timing telemetry")
        if value.get("event") == "ndi_bank_timing":
            answer.append(value)
    return answer


def evaluate_stage(
    name: str,
    duration: float,
    before: dict[str, Any],
    after: dict[str, Any],
    samples: list[dict[str, Any]],
    timings: list[dict[str, Any]],
    *,
    max_hold_ratio: float,
    min_fps: float,
    max_fps: float,
    max_bank_cpu_percent: float,
    max_bank_rss_bytes: float,
    max_core_cpu_percent: float,
    max_core_rss_bytes: float,
    min_host_idle_percent: float,
    max_rss_growth_bytes: float,
    max_rss_growth_ratio: float,
    flow_ids: list[str],
) -> dict[str, Any]:
    if duration <= 0 or not samples or not timings:
        raise QualificationError("stage lacks duration, samples, or timing telemetry")
    deltas = {key: _counter_delta(before, after, key) for key in HARD_COUNTERS}
    routed = _counter_delta(before, after, "routed")
    held = _counter_delta(before, after, "held")
    start_heads, end_heads = _heads(before, flow_ids), _heads(after, flow_ids)
    fps = [(end - start) / duration for start, end in zip(start_heads, end_heads)]
    hold_ratio = held / (held + routed) if held + routed else 1.0
    max_skew = max(int(sample["skew_frames"]) for sample in samples)
    max_cpu = max(float(sample["bank_cpu_percent"]) for sample in samples)
    max_rss = max(float(sample["bank_rss_bytes"]) for sample in samples)
    max_core_cpu = max(float(sample["core_cpu_percent"]) for sample in samples)
    max_core_rss = max(float(sample["core_rss_bytes"]) for sample in samples)
    min_idle = min(float(sample["host_idle_percent"]) for sample in samples)
    window = min(2, len(samples))
    bank_first = statistics.median(float(item["bank_rss_bytes"]) for item in samples[:window])
    bank_last = statistics.median(float(item["bank_rss_bytes"]) for item in samples[-window:])
    core_first = statistics.median(float(item["core_rss_bytes"]) for item in samples[:window])
    core_last = statistics.median(float(item["core_rss_bytes"]) for item in samples[-window:])
    bank_growth = bank_last - bank_first
    core_growth = core_last - core_first
    bank_growth_limit = max(max_rss_growth_bytes, bank_first * max_rss_growth_ratio)
    core_growth_limit = max(max_rss_growth_bytes, core_first * max_rss_growth_ratio)
    restarts = max(int(sample["bank_restarts"]) for sample in samples)
    core_restarts = max(int(sample["core_restarts"]) for sample in samples)
    catchup = sum(int(item.get("catchup_ticks", 0)) for item in timings)
    frames_behind = max(int(item.get("max_frames_behind", 0)) for item in timings)
    minimum_timing_reports = max(1, int(duration) // 12)
    checks = {
        "duration_complete": duration >= 0.99 * (60 if name == "smoke" else 180),
        "routed_progress": routed > 0,
        "head_cadence": all(min_fps <= rate <= max_fps for rate in fps),
        "source_skew": max_skew <= 1,
        "hold_ratio": hold_ratio <= max_hold_ratio,
        "hard_counters": all(value == 0 for value in deltas.values()),
        "alarms_clear": after.get("sustained_unavailable_active") == 0,
        "no_restarts": restarts == 0 and core_restarts == 0,
        "no_catchup": catchup == 0 and frames_behind == 0,
        "timing_coverage": len(timings) >= minimum_timing_reports,
        "bank_cpu": max_cpu <= max_bank_cpu_percent,
        "bank_rss": max_rss <= max_bank_rss_bytes,
        "bank_rss_growth": bank_growth <= bank_growth_limit,
        "core_cpu": max_core_cpu <= max_core_cpu_percent,
        "core_rss": max_core_rss <= max_core_rss_bytes,
        "core_rss_growth": core_growth <= core_growth_limit,
        "host_idle": min_idle >= min_host_idle_percent,
    }
    return {
        "stage": name, "passed": all(checks.values()), "checks": checks,
        "duration_seconds": round(duration, 3), "samples": len(samples),
        "routed": routed, "held": held, "hold_ratio": round(hold_ratio, 6),
        "source_fps": [round(value, 3) for value in fps], "max_source_skew_frames": max_skew,
        "hard_counter_deltas": deltas, "catchup_ticks": catchup,
        "max_frames_behind": frames_behind, "max_bank_cpu_percent": round(max_cpu, 2),
        "max_bank_rss_mib": round(max_rss / 1024 / 1024, 2),
        "max_core_cpu_percent": round(max_core_cpu, 2),
        "max_core_rss_mib": round(max_core_rss / 1024 / 1024, 2),
        "bank_rss_growth_mib": round(bank_growth / 1024 / 1024, 2),
        "core_rss_growth_mib": round(core_growth / 1024 / 1024, 2),
        "minimum_host_idle_percent": round(min_idle, 2),
        "timing_reports": len(timings), "minimum_timing_reports": minimum_timing_reports,
    }


class Harness:
    def __init__(self, settings: Settings, *, docker: Docker | None = None,
                 fetch: Fetch = fetch_json, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 port_check: Callable[[str], bool] = port_free,
                 cpu_reader: CpuReader = read_host_cpu) -> None:
        self.s = settings; self.docker = docker or Docker(); self.fetch = fetch
        self.clock = clock; self.sleep = sleep; self.port_check = port_check
        self.cpu_reader = cpu_reader
        self.image_ids: dict[str, str] = {}; self.containers: dict[str, str] = {}
        self.flow_ids: list[str] = []
        self.owner_nonce = uuid.uuid4().hex

    def preflight(self) -> dict[str, Any]:
        self.flow_ids = validate_settings(self.s)
        self.docker.read("info", "--format", "{{.ServerVersion}}")
        if self.docker.read("ps", "--quiet"):
            raise QualificationError("clean-host qualification refuses while any container is running")
        if not self.port_check(self.s.bank_url) or not self.port_check(self.s.core_url):
            raise QualificationError("a required loopback health port is occupied")
        for role, image in (("bank", self.s.bank_image), ("core", self.s.core_image)):
            self.image_ids[role] = self.docker.image_id(image)
        for name in (self.s.bank_name, self.s.core_name):
            if self.docker.inspect_optional(name) is not None:
                raise QualificationError("an exact qualification container name already exists")
        return {"source_count": 2, "stages_seconds": [self.s.smoke_seconds, self.s.gate_seconds],
                "copy_workers": 1, "writer_mode": "native", "applied": False}

    def _create_domain(self) -> None:
        self.s.domain.mkdir(mode=0o700)
        (self.s.domain / "options.json").write_text(
            json.dumps({HISTORY_OPTION: 1_000_000_000}) + "\n", encoding="utf-8")
        (self.s.domain / ".qualification-owner").write_text(
            json.dumps({"deployment_id": self.s.deployment_id, "nonce": self.owner_nonce}) + "\n",
            encoding="utf-8")

    def _launch(self, role: str, name: str, args: list[str]) -> None:
        container_id = self.docker.mutate(
            "run", "--detach", "--name", name, "--network", "host", "--restart", "no",
            "--label", f"{OWNER_LABEL}={self.s.deployment_id}", "--label", f"{ROLE_LABEL}={role}",
            *args,
        )
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise QualificationError("Docker did not return a container ID")
        self.containers[role] = container_id

    def start(self) -> None:
        self._create_domain(); self.s.evidence_dir.mkdir(mode=0o700)
        self._launch("bank", self.s.bank_name, [
            "--mount", f"type=bind,source={self.s.domain},target=/mxl-domain",
            "--mount", f"type=bind,source={self.s.source_config.resolve()},target=/run/ndi-bank.json,readonly",
            "--entrypoint", "python3", self.image_ids["bank"],
            "/opt/mxl-switcher-extensions/inputs/ndi/ndi_bank.py",
            "--domain", "/mxl-domain", "--config", "/run/ndi-bank.json",
            "--rate-mode", "normalize", "--timestamp-mode", "receive-time",
            "--writer-mode", "native", "--copy-workers", "1",
            "--timing-report-seconds", "10", "--max-restarts", "0",
            "--bind", str(urllib.parse.urlparse(self.s.bank_url).hostname),
            "--port", str(urllib.parse.urlparse(self.s.bank_url).port),
        ])
        self._launch("core", self.s.core_name, [
            "--mount", f"type=bind,source={self.s.domain},target=/mxl-domain",
            "--mount", f"type=bind,source={self.s.core_config.resolve()},target=/etc/mxl-switcher-core.conf,readonly",
            "--mount", f"type=bind,source={self.s.definitions.resolve()},target=/etc/mxl-switcher-core/definitions,readonly",
            self.image_ids["core"], "--config", "/etc/mxl-switcher-core.conf",
        ])

    def _inspect_owned(self, role: str) -> dict[str, Any]:
        container_id = self.containers.get(role)
        if not container_id:
            raise QualificationError(f"owned {role} container was not recorded")
        value = self.docker.inspect_optional(container_id)
        if value is None:
            raise QualificationError(f"owned {role} container disappeared")
        labels = value.get("Config", {}).get("Labels") or {}
        if (value.get("Id") != self.containers.get(role) or
                labels.get(OWNER_LABEL) != self.s.deployment_id or labels.get(ROLE_LABEL) != role or
                value.get("Image") != self.image_ids.get(role)):
            raise QualificationError(f"ownership changed for {role}; refusing mutation")
        return value

    def wait_ready(self, timeout: float = 45.0) -> None:
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            try:
                bank = self.fetch(self.s.bank_url + "/api/v1/health/ready")
                core = self.fetch(self.s.core_url + "/health")
                if bank.get("ready") is True and core.get("status") == "running": return
            except (OSError, ValueError, json.JSONDecodeError, QualificationError):
                pass
            self.sleep(1)
        raise QualificationError("candidate readiness timed out")

    def _sample(self) -> dict[str, Any]:
        if self.docker.running_ids() != set(self.containers.values()):
            raise QualificationError("clean-host container set changed during qualification")
        bank_i, core_i = self._inspect_owned("bank"), self._inspect_owned("core")
        bank_state = self.fetch(self.s.bank_url + "/api/v1/state")
        health = self.fetch(self.s.core_url + "/health")
        if bank_state.get("ready") is not True or health.get("status") != "running":
            raise QualificationError("candidate became unready")
        heads = _heads(health, self.flow_ids)
        stats = self.docker.stats([self.s.bank_name, self.s.core_name])
        if self.s.bank_name not in stats or self.s.core_name not in stats:
            raise QualificationError("candidate resource statistics are incomplete")
        idle, total = self.cpu_reader()
        previous_idle, previous_total = self._previous_cpu
        total_delta, idle_delta = total - previous_total, idle - previous_idle
        if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
            raise QualificationError("host CPU accounting did not advance")
        self._previous_cpu = (idle, total)
        return {"skew_frames": abs(heads[1] - heads[0]),
                "bank_cpu_percent": stats[self.s.bank_name][0],
                "bank_rss_bytes": stats[self.s.bank_name][1],
                "core_cpu_percent": stats[self.s.core_name][0],
                "core_rss_bytes": stats[self.s.core_name][1],
                "host_idle_percent": 100.0 * idle_delta / total_delta,
                "bank_restarts": bank_i.get("RestartCount", -1),
                "core_restarts": core_i.get("RestartCount", -1)}

    def run_stage(self, name: str, seconds: int) -> dict[str, Any]:
        initial_log_count = len(timing_events(self.docker.logs(self.s.bank_name)))
        before = self.fetch(self.s.core_url + "/health")
        self._previous_cpu = self.cpu_reader()
        started = self.clock(); samples = []
        while self.clock() - started < seconds:
            self.sleep(min(self.s.interval_seconds, max(0, seconds - (self.clock() - started))))
            samples.append(self._sample())
        duration = self.clock() - started
        after = self.fetch(self.s.core_url + "/health")
        timings = timing_events(self.docker.logs(self.s.bank_name))[initial_log_count:]
        result = evaluate_stage(
            name, duration, before, after, samples, timings,
            max_hold_ratio=self.s.max_hold_ratio, min_fps=self.s.min_fps, max_fps=self.s.max_fps,
            max_bank_cpu_percent=self.s.max_bank_cpu_percent,
            max_bank_rss_bytes=self.s.max_bank_rss_bytes,
            max_core_cpu_percent=self.s.max_core_cpu_percent,
            max_core_rss_bytes=self.s.max_core_rss_bytes,
            min_host_idle_percent=self.s.min_host_idle_percent,
            max_rss_growth_bytes=self.s.max_rss_growth_bytes,
            max_rss_growth_ratio=self.s.max_rss_growth_ratio,
            flow_ids=self.flow_ids,
        )
        (self.s.evidence_dir / f"{name}.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result

    def qualify(self) -> dict[str, Any]:
        smoke = self.run_stage("smoke", self.s.smoke_seconds)
        stages = [smoke]
        if smoke["passed"]:
            stages.append(self.run_stage("gate", self.s.gate_seconds))
        result = {"schema": "mxl-ndi-clean-host-v1",
                  "passed": len(stages) == 2 and all(stage["passed"] for stage in stages),
                  "source_count": 2, "writer_mode": "native", "copy_workers": 1,
                  "image_ids": dict(self.image_ids),
                  "stages": stages}
        (self.s.evidence_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result

    def cleanup(self) -> None:
        errors = []
        for role in ("core", "bank"):
            if role not in self.containers: continue
            try:
                self._inspect_owned(role)
                self.docker.mutate("rm", "--force", self.containers[role])
            except QualificationError as error:
                errors.append(str(error))
        if self.s.domain.exists() and not errors:
            try:
                marker = json.loads((self.s.domain / ".qualification-owner").read_text())
                if marker != {"deployment_id": self.s.deployment_id, "nonce": self.owner_nonce}:
                    raise QualificationError("domain ownership changed; refusing removal")
                shutil.rmtree(self.s.domain)
            except (OSError, json.JSONDecodeError, QualificationError):
                errors.append("unable to verify or remove the owned MXL domain")
        if errors: raise QualificationError("; ".join(errors))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--bank-image", required=True); parser.add_argument("--core-image", required=True)
    parser.add_argument("--source-config", type=pathlib.Path, required=True)
    parser.add_argument("--core-config", type=pathlib.Path, required=True)
    parser.add_argument("--definitions", type=pathlib.Path, required=True)
    parser.add_argument("--domain-root", type=pathlib.Path, default=pathlib.Path("/dev/shm"))
    parser.add_argument("--evidence-dir", type=pathlib.Path, required=True)
    parser.add_argument("--bank-url", default="http://127.0.0.1:18130")
    parser.add_argument("--core-url", default="http://127.0.0.1:18131/api/mxl/v1")
    parser.add_argument("--interval-seconds", type=int, default=10)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> Settings:
    return Settings(args.deployment_id, args.bank_image, args.core_image, args.source_config,
                    args.core_config, args.definitions, args.domain_root, args.evidence_dir,
                    loopback_url(args.bank_url), loopback_url(args.core_url),
                    60, 180, args.interval_seconds)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        harness = Harness(settings_from_args(args))
        plan = harness.preflight()
        if not args.apply:
            print(json.dumps(plan, indent=2, sort_keys=True)); return 0
        try:
            harness.start(); harness.wait_ready(); result = harness.qualify()
        finally:
            harness.cleanup()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1
    except QualificationError as error:
        print(json.dumps({"passed": False, "error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError):
        print(json.dumps({"passed": False, "error": "private input or local I/O validation failed"},
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Guarded launcher for the reproducible ZoomISO/Core operator demonstration."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[2]
FLOW_LABEL = re.compile(r"^ZoomISO Cloud Instance Output (\d+) Video$")
AUDIO_LABEL = re.compile(r"^ZoomISO Cloud Instance Output (\d+) Audio$")
SAFE_NAME = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
OWNED_SUFFIXES = (
    "core", "mediamtx", "program-confidence", "preview-confidence",
    "multiview-confidence", "skin", "youtube",
)
# Every container this launcher creates carries this label. Names alone are not
# proof of ownership: an unrelated service may already use the same prefix.
OWNER_LABEL = "io.github.guycochran.mxl-online-demo.deployment"
HARD_COUNTERS = (
    "invalid", "too_early", "too_late", "timeouts", "reader_reopens",
    "sync_timeouts", "sync_too_early", "sync_too_late", "no_common_window",
    "selected_input_regressions", "sustained_unavailable_alarms",
    "sustained_unavailable_active",
)


def read_json(path: pathlib.Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def config_path(value: str, base: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(os.path.expanduser(value))
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: pathlib.Path) -> dict:
    cfg = read_json(path)
    required = {
        "deployment_id", "source_count", "domain_host_path", "domain_mount_source",
        "base_image", "core_repository", "core_ref", "core_image", "operator_image",
        "youtube_image", "mediamtx_image", "core_port", "skin_port", "youtube_port",
        "rtmp_port", "mediamtx_api_port", "webrtc_port", "ice_tcp_port", "state_dir",
        "confidence_browser_base",
    }
    missing = sorted(required - cfg.keys())
    if missing:
        raise ValueError("missing configuration keys: " + ", ".join(missing))
    if not SAFE_NAME.fullmatch(str(cfg["deployment_id"])):
        raise ValueError("deployment_id must be a lowercase Docker-safe name")
    if cfg["source_count"] not in (4, 8):
        raise ValueError("source_count must be 4 or 8")
    cfg["config_file"] = str(path.resolve())
    cfg["state_dir"] = str(config_path(str(cfg["state_dir"]), path.parent))
    cfg["domain_host_path"] = str(config_path(str(cfg["domain_host_path"]), path.parent))
    configured_core = str(cfg.get("core_repo", "")).strip()
    cfg["core_repo_managed"] = not bool(configured_core)
    cfg["core_repo"] = str(
        config_path(configured_core, path.parent)
        if configured_core else pathlib.Path(cfg["state_dir"]) / "repos" / "mxl-switcher-core"
    )
    docker_value = cfg.get("docker", ["docker"])
    if not isinstance(docker_value, list) or not docker_value or not all(
        isinstance(item, str) and item for item in docker_value
    ):
        raise ValueError("docker must be a non-empty JSON string array")
    cfg["docker"] = docker_value
    port_keys = (
        "core_port", "skin_port", "youtube_port", "rtmp_port",
        "mediamtx_api_port", "webrtc_port", "ice_tcp_port",
    )
    for key in port_keys:
        port = int(cfg[key])
        if not 1024 <= port <= 65535:
            raise ValueError(f"{key} is outside 1024-65535")
        cfg[key] = port
    if len({cfg[k] for k in port_keys}) != len(port_keys):
        raise ValueError("all configured ports must be distinct")
    browser_base = str(cfg["confidence_browser_base"]).rstrip("/")
    if not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):[0-9]+", browser_base):
        raise ValueError("confidence_browser_base must be loopback HTTP with an explicit port")
    cfg["confidence_browser_base"] = browser_base
    return cfg


def quote(command: list[str]) -> str:
    return " ".join(shlex.quote(str(item)) for item in command)


def run(command: list[str], apply: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    print(("RUN  " if apply else "PLAN ") + quote(command))
    if not apply:
        return subprocess.CompletedProcess(command, 0, "", "")
    return subprocess.run(
        command, check=True, text=True, capture_output=capture,
    )


def docker(cfg: dict, *args: str) -> list[str]:
    return [*cfg["docker"], *map(str, args)]


def container_name(cfg: dict, suffix: str) -> str:
    return f"{cfg['deployment_id']}-{suffix}"


def container_owner(cfg: dict, name: str) -> str | None:
    """Return the owning deployment label of an existing container, or None if absent."""
    proc = subprocess.run(
        docker(cfg, "inspect", "-f", f'{{{{index .Config.Labels "{OWNER_LABEL}"}}}}', name),
        text=True, capture_output=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def discover(cfg: dict, active_seconds: float = 30.0) -> list[dict]:
    domain = pathlib.Path(cfg["domain_host_path"])
    if not domain.is_dir():
        raise RuntimeError(f"MXL domain is not readable: {domain}")
    now = time.time()
    videos: dict[tuple[int, str], tuple[float, dict]] = {}
    audio: dict[tuple[int, str], tuple[float, dict]] = {}
    for definition in domain.glob("*.mxl-flow/flow_def.json"):
        try:
            value = read_json(definition)
            data = definition.parent / "data"
            age = now - data.stat().st_mtime
            if age > active_seconds:
                continue
            label = str(value.get("label", ""))
            media_type = value.get("media_type")
            if media_type == "video/v210":
                match = FLOW_LABEL.match(label)
            elif media_type == "audio/float32":
                match = AUDIO_LABEL.match(label)
            else:
                continue
            if not match:
                continue
            number = int(match.group(1))
            device = str(value.get("device_id", ""))
            if not device:
                continue
            item = {"number": number, "device_id": device, "flow_id": str(value["id"]), "definition": value}
            target = videos if media_type == "video/v210" else audio
            key = (number, device)
            if key not in target or data.stat().st_mtime > target[key][0]:
                target[key] = (data.stat().st_mtime, item)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    result = []
    for number in sorted({key[0] for key in videos}):
        candidates = sorted(
            (value for key, value in videos.items() if key[0] == number),
            key=lambda value: value[0], reverse=True,
        )
        if not candidates:
            continue
        video = candidates[0][1]
        paired = audio.get((number, video["device_id"]))
        if paired:
            result.append({**video, "audio_flow_id": paired[1]["flow_id"]})
    return result[: cfg["source_count"]]


def output_definition(source_definition: dict, flow_id: str, label: str, group: str) -> dict:
    allowed = {
        "format", "media_type", "grain_rate", "frame_width", "frame_height",
        "interlace_mode", "colorspace", "components",
    }
    result = {key: source_definition[key] for key in allowed if key in source_definition}
    result.update({
        "id": flow_id,
        "label": label,
        "description": label + " generated by the guarded online-demo bundle",
        "parents": [],
        "tags": {"urn:x-nmos:tag:grouphint/v1.0": [group]},
    })
    return result


def prepare(cfg: dict) -> dict:
    sources = discover(cfg)
    if len(sources) != cfg["source_count"]:
        raise RuntimeError(
            f"required {cfg['source_count']} live paired ZoomISO outputs; found {len(sources)}"
        )
    state = pathlib.Path(cfg["state_dir"])
    definitions = state / "definitions"
    definitions.mkdir(parents=True, exist_ok=True)
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/guycochran/mxl-switcher-extensions")
    program_id = str(uuid.uuid5(namespace, cfg["deployment_id"] + ":program"))
    preview_id = str(uuid.uuid5(namespace, cfg["deployment_id"] + ":preview"))
    source_definition = sources[0]["definition"]
    program = output_definition(source_definition, program_id, "MXL Demo Program", "MXLDemoProgram:Video")
    preview = output_definition(source_definition, preview_id, "MXL Demo Preview", "MXLDemoPreview:Video")
    (definitions / "program.json").write_text(json.dumps(program, indent=2) + "\n", encoding="utf-8")
    (definitions / "preview.json").write_text(json.dumps(preview, indent=2) + "\n", encoding="utf-8")
    rate = source_definition.get("grain_rate", {"numerator": 30, "denominator": 1})
    lines = ["domain=/mxl-domain"]
    for index, source in enumerate(sources, 1):
        lines.append(f"source=zoomiso-{index}|{source['flow_id']}|ZoomISO {index}")
    lines.extend([
        "program_definition=/etc/mxl-switcher-core/definitions/program.json",
        f"program_id={program_id}",
        "preview_definition=/etc/mxl-switcher-core/definitions/preview.json",
        f"preview_id={preview_id}",
        "initial_program=zoomiso-1",
        f"initial_preview=zoomiso-{2 if len(sources) > 1 else 1}",
        f"width={int(source_definition.get('frame_width', 1920))}",
        f"height={int(source_definition.get('frame_height', 1080))}",
        f"rate_numerator={int(rate.get('numerator', 30))}",
        f"rate_denominator={int(rate.get('denominator', 1))}",
        "frames=0", "take_every=0",
        f"input_delay_frames={int(cfg.get('input_delay_frames', 1))}",
        "read_timeout_ns=5000000", "control_host=127.0.0.1",
        f"control_port={cfg['core_port']}",
    ])
    (state / "core.conf").write_text("\n".join(lines) + "\n", encoding="utf-8")
    mediamtx = "\n".join([
        "logLevel: info",
        "api: yes",
        f"apiAddress: 127.0.0.1:{cfg['mediamtx_api_port']}",
        "metrics: no", "pprof: no", "playback: no", "rtsp: no", "hls: no",
        "srt: no", "moq: no", "rtmp: yes",
        f"rtmpAddress: 127.0.0.1:{cfg['rtmp_port']}",
        "webrtc: yes",
        f"webrtcAddress: 127.0.0.1:{cfg['webrtc_port']}",
        'webrtcLocalUDPAddress: ""',
        f"webrtcLocalTCPAddress: 127.0.0.1:{cfg['ice_tcp_port']}",
        "webrtcIPsFromInterfaces: no", "webrtcAdditionalHosts: [127.0.0.1]",
        "paths:", "  mxl-program:", "  mxl-preview:", "  mxl-multiview:", "",
    ])
    (state / "mediamtx.yml").write_text(mediamtx, encoding="utf-8")
    resolved = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "program_flow_id": program_id,
        "preview_flow_id": preview_id,
        "sources": [{k: source[k] for k in ("number", "flow_id", "audio_flow_id")} for source in sources],
    }
    temporary = state / "resolved.json.new"
    temporary.write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")
    temporary.replace(state / "resolved.json")
    print(f"Prepared {len(sources)} paired sources in {state}")
    return resolved


def image_exists(cfg: dict, image: str) -> bool:
    return subprocess.run(docker(cfg, "image", "inspect", image), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def port_free(port: int) -> bool:
    sock = socket.socket()
    sock.settimeout(0.15)
    try:
        return sock.connect_ex(("127.0.0.1", port)) != 0
    finally:
        sock.close()


def doctor(cfg: dict, strict_images: bool = False) -> int:
    failures = 0
    print("MXL online-demo doctor")
    for program in (cfg["docker"][0], "git", "curl"):
        ok = shutil.which(program) is not None
        print(f"  {'PASS' if ok else 'FAIL'} executable {program}")
        failures += not ok
    docker_ok = subprocess.run(docker(cfg, "info"), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL).returncode == 0
    print(f"  {'PASS' if docker_ok else 'FAIL'} Docker API")
    failures += not docker_ok
    try:
        sources = discover(cfg)
    except RuntimeError as error:
        sources = []
        print(f"  FAIL {error}")
        failures += 1
    else:
        ok = len(sources) == cfg["source_count"]
        print(f"  {'PASS' if ok else 'FAIL'} live paired ZoomISO sources {len(sources)}/{cfg['source_count']}")
        failures += not ok
    for image in (
        cfg["base_image"], cfg["mediamtx_image"], cfg["core_image"],
        cfg["operator_image"], cfg["youtube_image"],
    ):
        ok = docker_ok and image_exists(cfg, image)
        level = "PASS" if ok else ("FAIL" if strict_images or image == cfg["base_image"] else "WARN")
        print(f"  {level} image {image}")
        failures += (not ok and (strict_images or image == cfg["base_image"]))
    cpu = os.cpu_count() or 0
    print(f"  {'PASS' if cpu >= 32 else 'WARN'} logical CPUs {cpu} (32 recommended)")
    return 1 if failures else 0


def bootstrap(cfg: dict, apply: bool) -> None:
    if not apply:
        raise RuntimeError("bootstrap changes local images and requires --apply")
    core = pathlib.Path(cfg["core_repo"])
    if not (core / ".git").is_dir():
        if not cfg["core_repo_managed"]:
            raise RuntimeError(f"configured core_repo is not a Git checkout: {core}")
        core.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--filter=blob:none", cfg["core_repository"], str(core)])
    dirty = subprocess.run(
        ["git", "-C", str(core), "status", "--porcelain"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(f"refusing to change a dirty Core checkout: {core}")
    if cfg["core_repo_managed"]:
        run(["git", "-C", str(core), "fetch", "origin", cfg["core_ref"]])
        run(["git", "-C", str(core), "checkout", "--detach", cfg["core_ref"]])
    else:
        head = subprocess.run(
            ["git", "-C", str(core), "rev-parse", "HEAD"],
            check=True, text=True, capture_output=True,
        ).stdout.strip()
        expected = subprocess.run(
            ["git", "-C", str(core), "rev-parse", cfg["core_ref"]],
            check=True, text=True, capture_output=True,
        ).stdout.strip()
        if head != expected:
            raise RuntimeError(
                f"configured Core checkout is {head[:12]}, expected {expected[:12]}; "
                "use a clean pinned checkout or leave core_repo blank"
            )
    run(docker(cfg, "build", "--pull=false", "-t", cfg["core_image"],
               "--build-arg", f"BASE_IMAGE={cfg['base_image']}",
               "-f", str(core / "packaging/easy-mxl/Dockerfile.runtime"), str(core)))
    run(docker(cfg, "build", "--pull=false", "-t", cfg["operator_image"],
               "--build-arg", f"BASE_IMAGE={cfg['base_image']}",
               "-f", str(ROOT / "deploy/online-demo/Dockerfile.operator"), str(core)))
    run(docker(cfg, "build", "--pull=false", "-t", cfg["youtube_image"],
               "--build-arg", f"BASE_IMAGE={cfg['base_image']}",
               "-f", str(ROOT / "outputs/rtmp/Dockerfile.cpu"), str(ROOT)))
    run(docker(cfg, "pull", cfg["mediamtx_image"]))
    run(docker(cfg, "run", "--rm", cfg["operator_image"], "-c",
               "import cv2, numpy; print('operator runtime: ok')"))


def ensure_launchable(cfg: dict) -> None:
    if doctor(cfg, strict_images=True):
        raise RuntimeError("preflight failed")
    for suffix in OWNED_SUFFIXES:
        name = container_name(cfg, suffix)
        if subprocess.run(docker(cfg, "inspect", name), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            raise RuntimeError(f"refusing existing container {name}; run stop explicitly")
    for key in (
        "core_port", "skin_port", "youtube_port", "rtmp_port",
        "mediamtx_api_port", "webrtc_port", "ice_tcp_port",
    ):
        if not port_free(cfg[key]):
            raise RuntimeError(f"port {cfg[key]} ({key}) is already occupied")


def wait_url(url: str, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 400:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise RuntimeError(f"endpoint did not become ready: {url}")


def commands(cfg: dict, resolved: dict) -> list[list[str]]:
    state = pathlib.Path(cfg["state_dir"])
    mount = f"{cfg['domain_mount_source']}:/mxl-domain"
    names = {suffix: container_name(cfg, suffix) for suffix in OWNED_SUFFIXES}
    # No restart policy: after a reboot or VM power-up the demo must stay down
    # until an operator relaunches it against freshly discovered flows, rather
    # than restart-looping on flow IDs from a previous meeting.
    common = ["--restart=no", "--label", f"{OWNER_LABEL}={cfg['deployment_id']}"]
    result = [
        docker(cfg, "run", "-d", "--name", names["core"], *common, "--network", "host",
               "--health-cmd", "python3 -c \"import urllib.request; "
               f"urllib.request.urlopen('http://127.0.0.1:{cfg['core_port']}/api/mxl/v1/health', timeout=1).read()\"",
               "--health-interval", "5s", "--health-timeout", "2s",
               "--health-start-period", "10s", "--health-retries", "3",
               "-v", mount, "-v", f"{state / 'core.conf'}:/etc/mxl-switcher-core.conf:ro",
               "-v", f"{state / 'definitions'}:/etc/mxl-switcher-core/definitions:ro",
               cfg["core_image"], "--config", "/etc/mxl-switcher-core.conf"),
        docker(cfg, "run", "-d", "--name", names["mediamtx"], *common, "--network", "host",
               "-v", f"{state / 'mediamtx.yml'}:/mediamtx.yml:ro",
               cfg["mediamtx_image"]),
    ]
    confidence = [
        ("program-confidence", "single", 2, 30, 2500,
         [("PROGRAM", resolved["program_flow_id"])], "mxl-program"),
        ("preview-confidence", "single", 3, 15, 1000,
         [("PREVIEW", resolved["preview_flow_id"])], "mxl-preview"),
        ("multiview-confidence", "wall", 6, 10, 1500,
         [(f"ZOOM {s['number']}", s["flow_id"]) for s in resolved["sources"]], "mxl-multiview"),
    ]
    for suffix, layout, factor, fps, bitrate, inputs, path in confidence:
        command = docker(cfg, "run", "-d", "--name", names[suffix], *common, "--network", "host",
                         "--cpus", "2", "-v", mount, cfg["operator_image"],
                         "/opt/mxl-operator/mxl_confidence_encoder.py", "--domain", "/mxl-domain",
                         "--layout", layout, "--factor", str(factor), "--fps", str(fps),
                         "--bitrate-kbps", str(bitrate), "--rtmp",
                         f"rtmp://127.0.0.1:{cfg['rtmp_port']}/{path}")
        for label, flow in inputs:
            command.extend(["--input", f"{label}={flow}"])
        result.append(command)
    result.extend([
        docker(cfg, "run", "-d", "--name", names["skin"], *common, "--network", "host",
               cfg["operator_image"], "/opt/mxl-operator/server.py",
               "--port", str(cfg["skin_port"]), "--core", f"http://127.0.0.1:{cfg['core_port']}",
               "--program-url", f"{cfg['confidence_browser_base']}/mxl-program/",
               "--preview-url", f"{cfg['confidence_browser_base']}/mxl-preview/",
               "--multiview-url", f"{cfg['confidence_browser_base']}/mxl-multiview/"),
        docker(cfg, "run", "-d", "--name", names["youtube"], *common,
               "-p", f"127.0.0.1:{cfg['youtube_port']}:18121", "-v", mount,
               cfg["youtube_image"], "--domain", "/mxl-domain", "--source-count",
               str(cfg["source_count"]), "--mix-source-audio", "--audio-flow",
               resolved["sources"][0]["audio_flow_id"], "--video-encoder", "x264enc",
               "--video-bitrate-kbps", "30000", "--bind", "0.0.0.0", "--port", "18121",
               "--container-bind"),
    ])
    for source in resolved["sources"]:
        result[-1].extend(["--preferred-flow", source["flow_id"]])
    return result


def launch(cfg: dict, apply: bool) -> None:
    if not apply:
        raise RuntimeError("launch changes running containers and requires --apply")
    ensure_launchable(cfg)
    resolved = prepare(cfg)
    planned = commands(cfg, resolved)
    started: list[str] = []
    try:
        for index, command in enumerate(planned):
            run(command)
            started.append(command[command.index("--name") + 1])
            if index == 0:
                wait_url(f"http://127.0.0.1:{cfg['core_port']}/api/mxl/v1/health", 30)
            elif index == 1:
                wait_url(f"http://127.0.0.1:{cfg['mediamtx_api_port']}/v3/config/global/get", 20)
        wait_url(f"http://127.0.0.1:{cfg['skin_port']}/api/skin/state", 20)
        wait_url(f"http://127.0.0.1:{cfg['youtube_port']}/api/state", 20)
    except Exception:
        if started:
            subprocess.run(docker(cfg, "rm", "-f", *reversed(started)), check=False)
        raise
    print("READY: operator Skin and YouTube controller are loopback-only")


def status(cfg: dict) -> int:
    failures = 0
    for suffix in OWNED_SUFFIXES:
        name = container_name(cfg, suffix)
        proc = subprocess.run(docker(cfg, "inspect", "-f", "{{.State.Status}}|{{.RestartCount}}", name),
                              text=True, capture_output=True)
        value = proc.stdout.strip() if proc.returncode == 0 else "missing"
        ok = value == "running|0"
        print(f"{'PASS' if ok else 'FAIL'} {name}: {value}")
        failures += not ok
    for label, url in (
        ("core", f"http://127.0.0.1:{cfg['core_port']}/api/mxl/v1/health"),
        ("mediamtx", f"http://127.0.0.1:{cfg['mediamtx_api_port']}/v3/config/global/get"),
        ("skin", f"http://127.0.0.1:{cfg['skin_port']}/api/skin/state"),
        ("youtube", f"http://127.0.0.1:{cfg['youtube_port']}/api/state"),
    ):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                ok = response.status < 400
        except (OSError, urllib.error.URLError):
            ok = False
        print(f"{'PASS' if ok else 'FAIL'} {label}: {url}")
        failures += not ok
    return 1 if failures else 0


def url_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=3) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError(f"endpoint did not return a JSON object: {url}")
    return value


def latest_container_stats(cfg: dict, suffix: str) -> dict:
    name = container_name(cfg, suffix)
    proc = subprocess.run(docker(cfg, "logs", name), check=True, text=True,
                          capture_output=True)
    latest = None
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("event") == "stats":
            latest = value
    if latest is None:
        raise RuntimeError(f"no confidence stats found for {name}")
    return latest


def acceptance_snapshot(cfg: dict) -> dict:
    containers = {}
    for suffix in OWNED_SUFFIXES:
        name = container_name(cfg, suffix)
        proc = subprocess.run(
            docker(cfg, "inspect", "-f",
                   f'{{{{.State.Status}}}}|{{{{.RestartCount}}}}|{{{{index .Config.Labels "{OWNER_LABEL}"}}}}',
                   name), text=True, capture_output=True,
        )
        containers[suffix] = proc.stdout.strip() if proc.returncode == 0 else "missing"
    confidence = {
        suffix: latest_container_stats(cfg, suffix)
        for suffix in ("program-confidence", "preview-confidence", "multiview-confidence")
    }
    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "containers": containers,
        "core": url_json(f"http://127.0.0.1:{cfg['core_port']}/api/mxl/v1/health"),
        "youtube": url_json(f"http://127.0.0.1:{cfg['youtube_port']}/api/state"),
        "confidence": confidence,
    }


def evaluate_acceptance(before: dict, after: dict, source_count: int,
                        deployment_id: str | None = None) -> dict:
    checks = {}
    expected_container = lambda value: value.split("|")[:2] == ["running", "0"]
    # The ownership label is deliberately included in the snapshot and must be non-empty.
    checks["containers_running_zero_restarts_owned"] = all(
        expected_container(value) and len(value.split("|")) == 3 and
        value.split("|")[2] not in ("", "<no value>") and
        (deployment_id is None or value.split("|")[2] == deployment_id)
        for value in after["containers"].values()
    )
    checks["core_running"] = after["core"].get("status") == "running"
    checks["core_routed_progress"] = int(after["core"].get("routed", 0)) > int(before["core"].get("routed", 0))
    deltas = {
        key: int(after["core"].get(key, 0)) - int(before["core"].get(key, 0))
        for key in HARD_COUNTERS
    }
    checks["core_hard_counters_unchanged"] = all(value == 0 for value in deltas.values())
    checks["core_hard_counters_zero"] = all(int(after["core"].get(key, 0)) == 0 for key in HARD_COUNTERS)
    confidence = {}
    for suffix, final in after["confidence"].items():
        first = before["confidence"][suffix]
        sources = final.get("sources", {})
        confidence[suffix] = {
            "frames_progress": int(final.get("frames", 0)) > int(first.get("frames", 0)),
            "encoder_restarts_zero": int(final.get("encoder_restarts", 0)) == 0,
            "terminal_torn_zero": all(int(row.get("torn", 0)) == 0 for row in sources.values()),
            "missing_zero": all(int(row.get("missing", 0)) == 0 for row in sources.values()),
            "all_sources_live": bool(sources) and all(bool(row.get("live")) for row in sources.values()),
        }
    checks["confidence"] = confidence
    youtube = after["youtube"]
    checks["youtube_sources_ready"] = int(youtube.get("source_count", 0)) >= source_count
    checks["youtube_audio_ready"] = bool(youtube.get("audio_ready"))
    output = youtube.get("output") or {}
    checks["youtube_output_ok"] = not youtube.get("running") or bool(output.get("ready"))
    flat = [value for key, value in checks.items() if key != "confidence"]
    flat.extend(value for group in confidence.values() for value in group.values())
    return {"passed": all(flat), "checks": checks, "hard_counter_deltas": deltas}


def acceptance(cfg: dict, seconds: float) -> int:
    if seconds <= 0:
        raise ValueError("acceptance duration must be positive")
    before = acceptance_snapshot(cfg)
    print(f"Observing the running demo for {seconds:g} seconds...")
    time.sleep(seconds)
    after = acceptance_snapshot(cfg)
    report = {
        "schema": "mxl-online-demo-acceptance-v1", "duration_s": seconds,
        **evaluate_acceptance(before, after, cfg["source_count"], cfg["deployment_id"]),
        "before": before, "after": after,
    }
    target = pathlib.Path(cfg["state_dir"]) / "acceptance-latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"{'PASS' if report['passed'] else 'FAIL'} acceptance report: {target}")
    return 0 if report["passed"] else 1


def stop(cfg: dict, apply: bool) -> None:
    if not apply:
        raise RuntimeError("stop removes the demo containers and requires --apply")
    existing = []
    foreign = []
    for suffix in OWNED_SUFFIXES:
        name = container_name(cfg, suffix)
        owner = container_owner(cfg, name)
        if owner is None:
            continue
        (existing if owner == cfg["deployment_id"] else foreign).append(name)
    if foreign:
        raise RuntimeError(
            "refusing to remove containers this launcher did not create: " + ", ".join(foreign)
        )
    if existing:
        run(docker(cfg, "rm", "-f", *existing))
    print("Stopped only: " + (", ".join(existing) if existing else "no owned containers found"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "prepare", "plan", "bootstrap", "launch", "status", "acceptance", "stop"))
    parser.add_argument("--config", type=pathlib.Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="observation window for the acceptance command")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.command == "doctor":
            return doctor(cfg)
        if args.command == "prepare":
            prepare(cfg)
        elif args.command == "bootstrap":
            bootstrap(cfg, args.apply)
        elif args.command == "launch":
            launch(cfg, args.apply)
        elif args.command == "status":
            return status(cfg)
        elif args.command == "acceptance":
            return acceptance(cfg, args.seconds)
        elif args.command == "stop":
            stop(cfg, args.apply)
        elif args.command == "plan":
            resolved_path = pathlib.Path(cfg["state_dir"]) / "resolved.json"
            resolved = read_json(resolved_path) if resolved_path.exists() else prepare(cfg)
            for command in commands(cfg, resolved):
                run(command, apply=False)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

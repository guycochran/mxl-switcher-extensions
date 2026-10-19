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
    "multiview-confidence", "layout-a", "layout-b", "skin", "youtube",
)
# Every container this launcher creates carries this label. Names alone are not
# proof of ownership: an unrelated service may already use the same prefix.
OWNER_LABEL = "io.github.guycochran.mxl-online-demo.deployment"
# Confidence encoders print a cumulative stats line at this interval. The
# acceptance gate compares the newest line before and after its window, so the
# window must span several lines or "frames advanced" and "live" are stale.
CONFIDENCE_STATS_EVERY_S = 5
MIN_ACCEPTANCE_SECONDS = 3 * CONFIDENCE_STATS_EVERY_S
# A flow counts as live when its ``data`` file was modified within this window.
# Writers update ``data`` through a shared mmap, so the kernel refreshes its
# mtime only when the dirty page is written back. With Ubuntu defaults
# (vm.dirty_expire_centisecs=3000, vm.dirty_writeback_centisecs=500) the
# observed mtime age of a healthy flow climbs to 30-35 s before resetting, so a
# 30 s window briefly marks every source stale. 60 s leaves a clear margin.
# A future check could confirm liveness from the advancing head index instead,
# which needs the MXL SDK.
FLOW_ACTIVE_SECONDS = 60.0
HARD_COUNTERS = (
    "invalid", "too_early", "too_late", "timeouts", "reader_reopens",
    "sync_timeouts", "sync_too_early", "sync_too_late", "no_common_window",
    "selected_input_regressions", "sustained_unavailable_alarms",
    "sustained_unavailable_active",
)
EXAMPLE_CONFIG = pathlib.Path(__file__).with_name("demo-config.example.json")
BASE_IMAGE_PROBE = r'''import ctypes
import pathlib
import shutil
import subprocess
import sys

missing = []
for command in ("make", "c++"):
    if not shutil.which(command):
        missing.append("command:" + command)
compiler = shutil.which("c++")
if compiler:
    check = subprocess.run(
        [compiler, "-std=c++20", "-x", "c++", "-fsyntax-only", "-"],
        input="int main(){ auto value = 0; return value; }\n",
        text=True, capture_output=True,
    )
    if check.returncode:
        missing.append("compiler:c++20")
for module in ("gi", "numpy", "cv2"):
    try:
        __import__(module)
    except Exception:
        missing.append("python:" + module)
try:
    ctypes.CDLL("libmxl.so")
except OSError:
    missing.append("library:libmxl.so")
try:
    ctypes.CDLL("libcairo.so.2")
except OSError:
    missing.append("library:libcairo.so.2")
headers = pathlib.Path("/src/mxl/lib/include/mxl")
if not all((headers / name).is_file() for name in ("dataformat.h", "flow.h", "time.h")):
    missing.append("headers:/src/mxl/lib/include/mxl")
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    if Gst.version() < (1, 24, 0, 0):
        missing.append("gstreamer:1.24+")
    required = (
        "mxlsrc", "mxlsink", "appsrc", "queue", "identity", "x264enc", "h264parse", "flvmux",
        "rtmpsink", "rtmp2sink", "compositor", "videoscale", "videoconvert",
        "videotestsrc", "cairooverlay", "audiomixer", "audioconvert",
        "audioresample", "rglimiter", "avenc_aac", "aacparse",
    )
    for element in required:
        if Gst.ElementFactory.find(element) is None:
            missing.append("gstreamer:" + element)
except Exception:
    missing.append("gstreamer:python-api")
if missing:
    print(", ".join(sorted(set(missing))))
    sys.exit(1)
print("contract-ok")
'''


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
    cfg["layout_image"] = str(cfg.get("layout_image", "mxl-layout-compositor:online-demo"))
    cfg["layout_bank_a_port"] = int(cfg.get("layout_bank_a_port", 9721))
    cfg["layout_bank_b_port"] = int(cfg.get("layout_bank_b_port", 9722))
    cfg["layout_bank_cpus"] = float(cfg.get("layout_bank_cpus", 4))
    if not 1 <= cfg["layout_bank_cpus"] <= 16:
        raise ValueError("layout_bank_cpus must be between 1 and 16")
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
        "layout_bank_a_port", "layout_bank_b_port",
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


ACCOUNT_HINT = (
    "run init and every later lifecycle command with one account that can read "
    "it, for example `sudo python3 deploy/online-demo/mxl_demo.py init`"
)
# Seconds to let freshly started containers settle before confirming none exited.
LAUNCH_SETTLE_SECONDS = 3.0


def readable_dir(path: pathlib.Path) -> bool:
    """True only for a directory this account can list.

    Docker volumes live below a root-only directory by default. Python 3.12's
    ``Path.is_dir`` raises PermissionError there instead of returning False.
    """
    try:
        return path.is_dir() and os.access(path, os.R_OK | os.X_OK)
    except PermissionError:
        return False


def docker_usable(command: list[str]) -> bool:
    return subprocess.run([*command, "info"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def detect_docker_command() -> list[str]:
    if shutil.which("docker") and docker_usable(["docker"]):
        return ["docker"]
    if shutil.which("sudo") and shutil.which("docker") and docker_usable(["sudo", "-n", "docker"]):
        return ["sudo", "-n", "docker"]
    raise RuntimeError(
        "Docker is not reachable. Install/start Docker, or grant this account "
        "access to it, then rerun init."
    )


def volume_mountpoint(docker_command: list[str], name: str) -> str:
    proc = subprocess.run(
        [*docker_command, "volume", "inspect", "-f", "{{.Mountpoint}}", name],
        check=True, text=True, capture_output=True,
    )
    mountpoint = proc.stdout.strip()
    if not mountpoint:
        raise RuntimeError(f"Docker volume has no mountpoint: {name}")
    return mountpoint


def validate_domain_binding(cfg: dict) -> str:
    """Require host discovery and container mounting to address one domain."""
    source = str(cfg["domain_mount_source"])
    if os.path.isabs(source):
        mounted_path = os.path.realpath(source)
    else:
        try:
            mounted_path = os.path.realpath(volume_mountpoint(cfg["docker"], source))
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f"cannot resolve configured MXL volume {source!r}") from error
    discovered_path = os.path.realpath(str(cfg["domain_host_path"]))
    if mounted_path != discovered_path:
        raise RuntimeError(
            "MXL domain mismatch: discovery reads "
            f"{discovered_path}, but containers would mount {source} ({mounted_path}); "
            "rerun init and do not edit the two domain fields independently"
        )
    return discovered_path


def discover_domain_volumes(docker_command: list[str]) -> list[str]:
    proc = subprocess.run([*docker_command, "volume", "ls", "--format", "{{.Name}}"],
                          check=True, text=True, capture_output=True)
    strong, likely = [], []
    for name in sorted(filter(None, (line.strip() for line in proc.stdout.splitlines()))):
        if re.search(r"(?:zoom|mxl)", name, re.IGNORECASE):
            likely.append(name)
        try:
            root = pathlib.Path(volume_mountpoint(docker_command, name))
            if root.is_dir() and next(root.glob("*.mxl-flow/flow_def.json"), None):
                strong.append(name)
        except (OSError, RuntimeError, subprocess.CalledProcessError):
            continue
    return strong or likely


def init_config(path: pathlib.Path, *, domain_volume: str | None,
                domain_path: str | None,
                base_image: str, source_count: int, force: bool) -> None:
    if path.exists() and not force:
        raise RuntimeError(f"refusing to overwrite {path}; use --force to replace it")
    docker_command = detect_docker_command()
    if domain_volume and domain_path:
        raise RuntimeError("choose exactly one of --domain-volume or --domain-path")
    candidates = discover_domain_volumes(docker_command) if not domain_path else []
    if not domain_volume and not domain_path:
        if len(candidates) == 1:
            domain_volume = candidates[0]
        else:
            detail = ", ".join(candidates) if candidates else "none detected"
            raise RuntimeError(
                "choose the ZoomISO MXL Docker volume with --domain-volume NAME "
                f"(candidates: {detail})"
            )
    if domain_path:
        mountpoint = str(pathlib.Path(os.path.expanduser(domain_path)).resolve())
        mount_source = mountpoint
    else:
        mountpoint = volume_mountpoint(docker_command, str(domain_volume))
        mount_source = str(domain_volume)
    if not readable_dir(pathlib.Path(mountpoint)):
        raise RuntimeError(f"MXL volume mountpoint is not readable: {mountpoint}; {ACCOUNT_HINT}")
    has_flow_definitions = next(pathlib.Path(mountpoint).glob("*.mxl-flow/flow_def.json"), None)
    if not image_exists({"docker": docker_command}, base_image):
        raise RuntimeError(
            f"operator-supplied base image {base_image!r} is not installed; load/build it "
            "or select the correct image with --base-image"
        )
    config = read_json(EXAMPLE_CONFIG)
    config.update({
        "source_count": source_count,
        "domain_host_path": mountpoint,
        "domain_mount_source": mount_source,
        "docker": docker_command,
        "base_image": base_image,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    # Parse the result before presenting it as usable.
    load_config(path)
    print(f"Wrote {path}")
    print(f"  Docker: {' '.join(docker_command)}")
    print(f"  MXL domain: {mount_source}")
    print(f"  Sources: {source_count}")
    if not has_flow_definitions:
        print("WARN: the selected domain has no flow definitions yet; doctor will "
              f"verify this exact source after ZoomISO mapping: {mount_source}")
    print("NEXT: map matching ZoomISO MXL video/audio outputs, then run `./mxl-demo preflight`.")


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


def discover(cfg: dict, active_seconds: float = FLOW_ACTIVE_SECONDS) -> list[dict]:
    domain = pathlib.Path(validate_domain_binding(cfg))
    if not readable_dir(domain):
        raise RuntimeError(f"MXL domain is not readable: {domain}; {ACCOUNT_HINT}")
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
    layout_a_id = str(uuid.uuid5(namespace, cfg["deployment_id"] + ":layout-a"))
    layout_b_id = str(uuid.uuid5(namespace, cfg["deployment_id"] + ":layout-b"))
    source_definition = sources[0]["definition"]
    source_format = (
        int(source_definition.get("frame_width", 1920)),
        int(source_definition.get("frame_height", 1080)),
        int(source_definition.get("grain_rate", {}).get("numerator", 30)),
        int(source_definition.get("grain_rate", {}).get("denominator", 1)),
    )
    for source in sources[1:]:
        definition = source["definition"]
        candidate = (
            int(definition.get("frame_width", 1920)),
            int(definition.get("frame_height", 1080)),
            int(definition.get("grain_rate", {}).get("numerator", 30)),
            int(definition.get("grain_rate", {}).get("denominator", 1)),
        )
        if candidate != source_format:
            raise RuntimeError(
                "layout inputs must share one width, height, and grain rate; "
                f"source {source['number']} is {candidate}, expected {source_format}"
            )
    program = output_definition(source_definition, program_id, "MXL Demo Program", "MXLDemoProgram:Video")
    preview = output_definition(source_definition, preview_id, "MXL Demo Preview", "MXLDemoPreview:Video")
    layout_a = output_definition(source_definition, layout_a_id, "MXL Layout Bank A", "MXLLayoutA:Video")
    layout_b = output_definition(source_definition, layout_b_id, "MXL Layout Bank B", "MXLLayoutB:Video")
    (definitions / "program.json").write_text(json.dumps(program, indent=2) + "\n", encoding="utf-8")
    (definitions / "preview.json").write_text(json.dumps(preview, indent=2) + "\n", encoding="utf-8")
    (definitions / "layout-a.json").write_text(json.dumps(layout_a, indent=2) + "\n", encoding="utf-8")
    (definitions / "layout-b.json").write_text(json.dumps(layout_b, indent=2) + "\n", encoding="utf-8")
    rate = source_definition.get("grain_rate", {"numerator": 30, "denominator": 1})
    lines = ["domain=/mxl-domain"]
    for index, source in enumerate(sources, 1):
        lines.append(f"source=zoomiso-{index}|{source['flow_id']}|ZoomISO {index}")
    lines.extend([
        f"source=supersource-a|{layout_a_id}|Layout Bank A",
        f"source=supersource-b|{layout_b_id}|Layout Bank B",
    ])
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
        "layout_a_flow_id": layout_a_id,
        "layout_b_flow_id": layout_b_id,
        "frame_width": int(source_definition.get("frame_width", 1920)),
        "frame_height": int(source_definition.get("frame_height", 1080)),
        "rate_numerator": int(rate.get("numerator", 30)),
        "rate_denominator": int(rate.get("denominator", 1)),
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


def base_image_contract(cfg: dict) -> tuple[bool, str]:
    """Probe the operator-controlled local base without exposing its contents."""
    proc = subprocess.run(
        docker(cfg, "run", "--rm", "--entrypoint", "python3", cfg["base_image"],
               "-c", BASE_IMAGE_PROBE),
        text=True, capture_output=True,
    )
    detail = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, detail or "probe exited without details"


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
    for program in (cfg["docker"][0], "git"):
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
        print(f"  {'PASS' if ok else 'FAIL'} live paired ZoomISO sources "
              f"{len(sources)}/{cfg['source_count']} from {cfg['domain_mount_source']}")
        failures += not ok
    for image in (
        cfg["base_image"], cfg["mediamtx_image"], cfg["core_image"],
        cfg["operator_image"], cfg["layout_image"], cfg["youtube_image"],
    ):
        ok = docker_ok and image_exists(cfg, image)
        level = "PASS" if ok else ("FAIL" if strict_images or image == cfg["base_image"] else "WARN")
        print(f"  {level} image {image}")
        failures += (not ok and (strict_images or image == cfg["base_image"]))
    if docker_ok and image_exists(cfg, cfg["base_image"]):
        ok, detail = base_image_contract(cfg)
        print(f"  {'PASS' if ok else 'FAIL'} operator base-image contract"
              + ("" if ok else f": {detail}"))
        failures += not ok
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
    operator_base = cfg["operator_image"] + "-core-base"
    run(docker(cfg, "build", "--pull=false", "-t", operator_base,
               "--build-arg", f"BASE_IMAGE={cfg['base_image']}",
               "-f", str(ROOT / "deploy/online-demo/Dockerfile.operator"), str(core)))
    run(docker(cfg, "build", "--pull=false", "-t", cfg["operator_image"],
               "--build-arg", f"OPERATOR_BASE={operator_base}",
               "-f", str(ROOT / "deploy/online-demo/Dockerfile.operator-layout"), str(ROOT)))
    run(docker(cfg, "build", "--pull=false", "-t", cfg["layout_image"],
               "--build-arg", f"BASE_IMAGE={cfg['base_image']}",
               "-f", str(ROOT / "processing/layout-compositor/Dockerfile"), str(ROOT)))
    run(docker(cfg, "build", "--pull=false", "-t", cfg["youtube_image"],
               "--build-arg", f"BASE_IMAGE={cfg['base_image']}",
               "-f", str(ROOT / "outputs/rtmp/Dockerfile.cpu"), str(ROOT)))
    run(docker(cfg, "pull", cfg["mediamtx_image"]))
    run(docker(cfg, "run", "--rm", cfg["operator_image"], "-c",
               "import cv2, numpy; print('operator runtime: ok')"))


def ensure_launchable(cfg: dict) -> None:
    if doctor(cfg, strict_images=True):
        raise RuntimeError("preflight failed")
    ensure_no_conflicts(cfg)


def ensure_no_conflicts(cfg: dict) -> None:
    """Refuse existing demo containers or occupied ports before any slow work."""
    for suffix in OWNED_SUFFIXES:
        name = container_name(cfg, suffix)
        if subprocess.run(docker(cfg, "inspect", name), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            raise RuntimeError(
                f"refusing existing container {name}; run `down --apply` if it is this "
                "demo's, or choose another deployment_id"
            )
    for key in (
        "core_port", "skin_port", "youtube_port", "rtmp_port",
        "mediamtx_api_port", "webrtc_port", "ice_tcp_port",
        "layout_bank_a_port", "layout_bank_b_port",
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
                         "--bitrate-kbps", str(bitrate),
                         "--stats-every-s", str(CONFIDENCE_STATS_EVERY_S), "--rtmp",
                         f"rtmp://127.0.0.1:{cfg['rtmp_port']}/{path}")
        for label, flow in inputs:
            command.extend(["--input", f"{label}={flow}"])
        result.append(command)
    initial = ["1", "2"]
    if len(resolved["sources"]) >= 4:
        second_initial = ["3", "4"]
    else:
        second_initial = initial
    for bank, port, output_flow, selected in (
        ("a", cfg["layout_bank_a_port"], resolved["layout_a_flow_id"], initial),
        ("b", cfg["layout_bank_b_port"], resolved["layout_b_flow_id"], second_initial),
    ):
        command = docker(
            cfg, "run", "-d", "--name", names[f"layout-{bank}"], *common,
            "--network", "host", "--cpus", f"{cfg['layout_bank_cpus']:g}", "-v", mount,
            cfg["layout_image"], "--bank", bank, "--domain", "/mxl-domain",
        )
        for source in resolved["sources"]:
            command.extend(["--input", source["flow_id"]])
        command.extend([
            "--output-flow-id", output_flow,
            "--compositor", "/opt/mxl-layout-compositor/layout_compositor.py",
            "--width", str(resolved["frame_width"]),
            "--height", str(resolved["frame_height"]),
            "--rate", f"{resolved['rate_numerator']}/{resolved['rate_denominator']}",
            "--control-host", "127.0.0.1", "--control-port", str(port),
            "--initial-count", "2", "--initial-sources", ",".join(selected),
        ])
        result.append(command)
    result.extend([
        docker(cfg, "run", "-d", "--name", names["skin"], *common, "--network", "host",
               "-e", f"MXL_LAYOUT_BANK_A_URL=http://127.0.0.1:{cfg['layout_bank_a_port']}",
               "-e", f"MXL_LAYOUT_BANK_B_URL=http://127.0.0.1:{cfg['layout_bank_b_port']}",
               cfg["operator_image"], "/opt/mxl-operator/server.py",
               "--port", str(cfg["skin_port"]), "--core", f"http://127.0.0.1:{cfg['core_port']}",
               "--supersource-helper", "/opt/mxl-operator/layout_bank_client.py",
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
        wait_url(f"http://127.0.0.1:{cfg['layout_bank_a_port']}/api/v1/health/ready", 20)
        wait_url(f"http://127.0.0.1:{cfg['layout_bank_b_port']}/api/v1/health/ready", 20)
        wait_url(f"http://127.0.0.1:{cfg['youtube_port']}/api/state", 20)
        verify_started(cfg, started)
    except Exception:
        if started:
            subprocess.run(docker(cfg, "rm", "-f", *reversed(started)), check=False)
        raise
    print("READY: operator Skin and YouTube controller are loopback-only")


def verify_started(cfg: dict, names: list[str]) -> None:
    """Fail the launch if any started container has already exited or restarted.

    The four HTTP waits cover Core, MediaMTX, Skin and YouTube only; a confidence
    encoder can exit at once (for example on a missing flow) while READY prints.
    Its log tail is saved privately before the caller removes the containers.
    """
    time.sleep(LAUNCH_SETTLE_SECONDS)
    failed = []
    for name in names:
        proc = subprocess.run(
            docker(cfg, "inspect", "-f", "{{.State.Status}}|{{.RestartCount}}", name),
            text=True, capture_output=True,
        )
        value = proc.stdout.strip() if proc.returncode == 0 else "missing"
        if value != "running|0":
            failed.append((name, value))
    if not failed:
        return
    state = pathlib.Path(cfg["state_dir"])
    state.mkdir(parents=True, exist_ok=True)
    saved = []
    for name, _value in failed:
        logs = subprocess.run(docker(cfg, "logs", "--tail", "100", name),
                              text=True, capture_output=True)
        target = state / f"launch-failure-{name}.log"
        target.write_text(logs.stdout + logs.stderr, encoding="utf-8")
        saved.append(str(target))
    detail = ", ".join(f"{name} ({value})" for name, value in failed)
    raise RuntimeError(
        f"started containers did not stay running: {detail}. Private log tails: "
        + ", ".join(saved) + ". The partial deployment was removed; fix the cause "
        "and rerun `up --apply`."
    )


def up(cfg: dict, apply: bool) -> None:
    if not apply:
        raise RuntimeError("up builds images and starts containers; rerun with --apply")
    if doctor(cfg):
        raise RuntimeError("doctor failed; fix the reported item before starting")
    # Check for leftovers and busy ports before spending minutes on image builds.
    ensure_no_conflicts(cfg)
    bootstrap(cfg, apply=True)
    launch(cfg, apply=True)


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
        ("layout-a", f"http://127.0.0.1:{cfg['layout_bank_a_port']}/api/v1/health/ready"),
        ("layout-b", f"http://127.0.0.1:{cfg['layout_bank_b_port']}/api/v1/health/ready"),
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
        "layouts": {
            bank: url_json(f"http://127.0.0.1:{port}/api/v1/state")
            for bank, port in (
                ("a", cfg["layout_bank_a_port"]),
                ("b", cfg["layout_bank_b_port"]),
            )
        },
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
    checks["layout_banks_ready_at_cadence"] = all(
        bool(final.get("ready"))
        and float(final.get("output_fps", 0)) >= 27
        and int(final.get("output_ticks", 0)) > int(before["layouts"][bank].get("output_ticks", 0))
        for bank, final in after["layouts"].items()
    )
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
    if seconds < MIN_ACCEPTANCE_SECONDS:
        raise ValueError(
            f"acceptance needs at least {MIN_ACCEPTANCE_SECONDS} seconds so each "
            f"confidence encoder reports fresh stats (every {CONFIDENCE_STATS_EVERY_S} s)"
        )
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
    parser = argparse.ArgumentParser(prog="./mxl-demo", description=__doc__)
    parser.add_argument("command", choices=(
        "init", "preflight", "doctor", "prepare", "plan", "bootstrap", "launch", "up",
        "status", "acceptance", "check", "stop", "down",
    ))
    parser.add_argument("--config", type=pathlib.Path,
                        default=pathlib.Path("demo-config.json"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="observation window for the acceptance command")
    parser.add_argument("--domain-volume",
                        help="ZoomISO Docker volume containing the live MXL domain")
    parser.add_argument("--domain-path",
                        help="host path containing the live MXL domain (bind-mount setups)")
    parser.add_argument("--base-image", default="mxl-lab:latest",
                        help="installed operator-supplied MXL runtime image")
    parser.add_argument("--source-count", type=int, choices=(4, 8), default=8)
    parser.add_argument("--force", action="store_true",
                        help="allow init to replace an existing config file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "init":
            init_config(args.config, domain_volume=args.domain_volume,
                        domain_path=args.domain_path,
                        base_image=args.base_image, source_count=args.source_count,
                        force=args.force)
            return 0
        cfg = load_config(args.config)
        if args.command in ("preflight", "doctor"):
            return doctor(cfg)
        if args.command == "prepare":
            prepare(cfg)
        elif args.command == "bootstrap":
            bootstrap(cfg, args.apply)
        elif args.command == "launch":
            launch(cfg, args.apply)
        elif args.command == "up":
            up(cfg, args.apply)
        elif args.command == "status":
            return status(cfg)
        elif args.command in ("acceptance", "check"):
            return acceptance(cfg, args.seconds)
        elif args.command in ("stop", "down"):
            stop(cfg, args.apply)
        elif args.command == "plan":
            # Always rediscover. Flow UUIDs rotate with ZoomISO sessions, and a
            # visually plausible plan made from yesterday's state is unsafe.
            resolved = prepare(cfg)
            for command in commands(cfg, resolved):
                run(command, apply=False)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

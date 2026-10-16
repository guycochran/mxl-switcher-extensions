# Guarded online-production deployment

This bundle packages the demonstrated ZoomISO → MXL Core → operator confidence
and YouTube path without pretending that licensed or secret-bearing steps can
be automated.

## What the launcher owns

All created containers use the configured `deployment_id` prefix:

| Suffix | Role |
| --- | --- |
| `-core` | Stable Program/Preview MXL routing and control API |
| `-mediamtx` | Loopback RTMP ingest and WebRTC confidence playback |
| `-program-confidence` | 960×540p30 Program monitor |
| `-preview-confidence` | 640×360p15 Preview monitor |
| `-multiview-confidence` | One 1280×720p10 4×2 wall—not eight browser encoders |
| `-skin` | Loopback ATEM-style browser control and EIC health |
| `-youtube` | Loopback 4K YouTube controller and CPU encoder |

ZoomISO and EasyMXL remain external. The stop command cannot remove them: it
removes only containers carrying this deployment's owner label and refuses any
same-named container without it. Demo containers use `--restart=no`, so a host
reboot or VM power-up never revives them against a previous meeting's flows.

## Safety model

- `doctor`, `prepare`, `plan`, and `status` are read-only outside the private
  state directory.
- `bootstrap`, `launch`, and `stop` require `--apply`.
- Launch refuses occupied ports, existing same-name containers, stale/missing
  source flows, non-loopback operator endpoints, or fewer than the requested
  paired video/audio sources.
- The stream key is entered only in the loopback YouTube controller and is
  never written by this launcher.
- Runtime UUIDs and host paths are kept under `state_dir`, not in Git.
- The launcher reads MXL metadata on the host before it starts containers;
  `domain_host_path` therefore must be readable by the launcher account.
- The Skin stays out of the media path. Losing it cannot stop Core.
- MediaMTX configuration is generated in the private state directory from the
  configured RTMP, API, WebRTC, and ICE ports, so an existing confidence stack
  does not need to be stopped. `confidence_browser_base` names the loopback
  address operators reach through their SSH tunnel.

## Multiview and ATEM Skin

The wall reads the same live MXL grains used by Core, decimates them before
encoding, and publishes one confidence stream. The ATEM-style Skin overlays
Program and Preview tally on that wall and provides direct Cut, Preview, and
Take controls.

The default `input_delay_frames=1` is the qualified ZoomISO operating point.
Together with Core's same-tick safety grain, it produces a fixed two-frame
presentation delay while leaving two slots of headroom in ZoomISO's six-grain
ring. Do not increase it casually: a three-frame effective delay reaches the
oldest safe slot and can become too late if the writer commits between the
runtime snapshot and the read.

The Skin also contains ME/SuperSource controls, but the default portable bundle
does not enable them. The original warm-bank helper depends on compositor files
that were host-local in the lab. The controls fail closed until a portable,
versioned compositor package is supplied. This is deliberate: a reproducible
direct switcher is better than a button that silently invokes missing software.

## Capacity

The demonstrated CPU-only 4K run used about 14.05–15.84 of 32 logical CPUs for
ZoomISO plus the output adapter. The separate confidence stack measured about
1.8–2.0 logical CPUs on an older host. Start with 32 modern logical CPUs and
leave headroom; at lower capacity, reduce or disable confidence monitoring
before compromising Program.

## Commands

```sh
python3 deploy/online-demo/mxl_demo.py doctor   --config demo-config.json
python3 deploy/online-demo/mxl_demo.py prepare  --config demo-config.json
python3 deploy/online-demo/mxl_demo.py plan     --config demo-config.json
python3 deploy/online-demo/mxl_demo.py bootstrap --config demo-config.json --apply
python3 deploy/online-demo/mxl_demo.py launch   --config demo-config.json --apply
python3 deploy/online-demo/mxl_demo.py status   --config demo-config.json
python3 deploy/online-demo/mxl_demo.py stop     --config demo-config.json --apply
```

`prepare` may be rerun after ZoomISO's periodic flow rotation. It replaces the
private resolved configuration atomically. Stop and relaunch this demo bundle
after rotation; do not edit flow UUIDs by hand.

## Honest completion boundary

The audience demonstration proved the eight-source 4K picture, smooth
post-compositor timecode, Program audio, RTMPS transport, and bounded CPU load.
The later eight-source audio mixer was deployed and detected, but still needs a
separate audience listening and long-run A/V synchronization gate. Treat this
bundle as a reproducible demonstration profile, not an unattended production
appliance.

## Fresh-host smoke result (2026-10-16)

The guarded bundle was exercised from bootstrap through cleanup on a clean,
CPU-only 32-vCPU/128-GiB cloud host while an unrelated operator stack remained
running. The generated, deployment-specific MediaMTX ports avoided that stack;
the launcher did not stop or replace any permanent service.

- `doctor` found eight live, uniquely paired ZoomISO video/audio sources.
- `bootstrap` built the pinned Core, operator, and CPU YouTube images and pulled
  the pinned MediaMTX image.
- `launch` started all seven owned containers with zero restarts. Core, the
  Skin, MediaMTX, and the YouTube controller all passed their endpoint checks.
- Program, Preview, and the eight-source multiview each advanced continuously.
  Their encoders reported no missing frames and no encoder restart.
- The YouTube controller reported 8/8 distinct video sources plus 8/8 live
  audio sources, ready for an operator-supplied destination and stream key.
- Preview and Take commands crossed the Skin-to-Core boundary successfully;
  Program was switched to a third input and restored to input 1.
- Five non-transmitting resource samples kept each owned service below 0.23 of
  one logical CPU and below 68 MiB RSS. This is a deployment/control smoke,
  not a measurement of the 4K encoder under transmission load.
- `stop` removed only the seven deployment-owned containers.

The run also recorded one transient Core `too_late` increment during the live
switch sequence and three torn-read retries on one multiview tile. Both then
stayed bounded, with no missing frames, reader failure, encoder restart,
sustained-unavailable alarm, or source regression. They remain explicit
long-run acceptance items; this smoke result must not be presented as the final
broadcast-readiness gate.

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

- `init` writes only the requested ignored local configuration file and refuses
  to replace one unless `--force` is explicit.
- `preflight`/`doctor`, `prepare`, `plan`, and `status` are read-only outside the private
  state directory.
- `bootstrap`, `launch`, `up`, `stop`, and `down` require `--apply`.
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

The Skin's ME controls drive two portable, versioned warm layout banks. Defined
presets are 2-up, 3-up, 4-up, 6-up, and 8-up. The operator prepares the off-air
bank, places its stable flow on Preview, and then takes it to Program; undefined
counts and under-cadence scenes fail closed. Bank geometry and grain rate are
inherited from the discovered ZoomISO sources, and mixed source formats are
rejected before launch. `layout_bank_cpus` sets each bank's CPU ceiling (default
4, allowed range 1–16); increase it only on a qualified modern host.

## Capacity

The demonstrated CPU-only 4K run used about 14.05–15.84 of 32 logical CPUs for
ZoomISO plus the output adapter. The separate confidence stack measured about
1.8–2.0 logical CPUs on an older host. Start with 32 modern logical CPUs and
leave headroom. A legacy host saturated a four-core bank and could not sustain
the multi-box presets, so cadence-qualified layout acceptance on the target host
is mandatory. At lower capacity, reduce or disable confidence monitoring before
compromising Program.

## Commands

```sh
python3 deploy/online-demo/mxl_demo.py init
python3 deploy/online-demo/mxl_demo.py doctor   --config demo-config.json
python3 deploy/online-demo/mxl_demo.py prepare  --config demo-config.json
python3 deploy/online-demo/mxl_demo.py plan     --config demo-config.json
python3 deploy/online-demo/mxl_demo.py bootstrap --config demo-config.json --apply
python3 deploy/online-demo/mxl_demo.py launch   --config demo-config.json --apply
python3 deploy/online-demo/mxl_demo.py status   --config demo-config.json
python3 deploy/online-demo/mxl_demo.py acceptance --config demo-config.json --seconds 30
python3 deploy/online-demo/mxl_demo.py stop     --config demo-config.json --apply
```

For first-time operators, the supported short path is `./mxl-demo init`,
`./mxl-demo preflight`, `./mxl-demo up --apply`, `./mxl-demo check`, and
`./mxl-demo down --apply`. `preflight` is a plain-language alias for `doctor`.
The longer commands above remain
available for engineering diagnosis and controlled rebuilds. See the
[top-level quickstart](../../QUICKSTART.md) and
[troubleshooting guide](TROUBLESHOOTING.md). The licensed image is an
operator-supplied prerequisite with a machine-checked
[base-image contract](BASE_IMAGE.md); `mxl-lab:latest` is not a public image.
The [prerequisite onboarding guide](PREREQUISITES.md) covers the
authorized-image, ZoomISO, host, security, and cloud handoff steps that must be
complete before this launcher takes control.
The [cross-cloud sizing guide](CLOUD_SIZING.md) names the Azure reference and
the comparable Google Cloud and AWS candidates, with one common acceptance
checklist for qualifying them.
The [outsider rehearsal](OUTSIDER_REHEARSAL.md) is the final packaging and
documentation gate before tagging a technical-preview release candidate.

`acceptance` is a read-only observation gate for a running deployment. It
captures two snapshots, requires every owned service to remain running with
zero restarts, verifies Core and all confidence outputs advance, enforces the
hard-zero Core and terminal-torn rules, and checks YouTube source/audio
readiness. It writes the full machine-readable result to the private
`state_dir/acceptance-latest.json`; the report contains runtime identifiers and
must not be committed.

Confidence encoders report cumulative stats every 5 seconds, and the gate
refuses windows shorter than 15 seconds, so both snapshots always compare fresh
frame counts and live state rather than a line printed up to a minute earlier.

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

That first run also recorded one transient Core `too_late` increment during the
live switch sequence and three rejected ring-race reads on one multiview tile.
The follow-up below closes both deployment-smoke caveats without weakening
either detector.

## Caveat-zero repeat (2026-10-16)

The same fresh-host sequence was repeated after pinning Core `29a7a9f` and
selecting the qualified two-frame ZoomISO operating point:

- all eight paired video/audio sources passed discovery;
- all seven owned services started and remained at zero restarts;
- Preview input 3, Take, Preview input 1, and Take restored Program input 1 at
  revision 4;
- `invalid`, `too_early`, `too_late`, `timeouts`, `reader_reopens`, all sync
  errors, `no_common_window`, input regressions, and sustained-unavailable
  counters remained zero from baseline through the switch and hold;
- Program, Preview, and all eight multiview inputs advanced with zero missing
  reads, zero terminal torn reads, and zero encoder restarts;
- six raw ring races on one multiview input were all recovered by the one-shot
  fresh-head retry. The retry and recovery counts remain visible in telemetry;
- the idle 4K controller found eight distinct video sources and eight live
  audio sources with `audio_ready=true`; no destination or key was supplied;
- sampled CPU stayed within 11.77–24.75% for Core, 22.53–26.29% for Program
  confidence, 6.22–8.54% for Preview confidence, and 10.21–11.67% for the
  eight-input multiview. Corresponding RSS ranges were 67.7–68.61 MiB,
  246.0–247.3 MiB, 97.37–97.95 MiB, and 55.93–56.62 MiB;
- cleanup removed only the seven labelled demo containers, permanent services
  remained untouched, and the paid test host was verified deallocated.

This is a clean deployment/control and confidence-path smoke. It still does
not replace a longer transmitted 4K Program and audience A/V-sync gate.

## Published operator rehearsal (2026-10-16)

The public `main` branch was then cloned into a new directory and exercised by
following the documented operator sequence rather than using a lab checkout.
The licensed runtime image and ZoomISO container were preloaded prerequisites;
no private source tree or saved runtime configuration was reused.
This was therefore a fresh public-checkout rehearsal on an existing lab host,
not a from-zero installation of Linux, Docker, or the licensed images.

- `init` selected the operator-supplied MXL domain and wrote a fresh ignored
  configuration; alternate loopback ports let the rehearsal coexist with an
  unrelated running stack. The only manual configuration changes were the
  documented `deployment_id`, loopback port fields, and absolute `state_dir`;
  a host without conflicting services can retain their defaults.
- `doctor` passed Docker, Git, the live 8/8 paired-source requirement, the
  complete operator-supplied base-image contract, the pinned MediaMTX image, and the
  32-logical-CPU capacity recommendation.
- The first `up --apply` cloned and pinned Core, built all three local images,
  and started all seven labelled services with zero restarts.
- The 30-second `check` passed every gate: Core routing advanced; every hard
  error counter remained zero and unchanged; Program, Preview, and all eight
  multiview inputs advanced with zero terminal torn or missing reads; and the
  YouTube controller found eight video plus eight audio sources ready for an
  operator-supplied destination.
- A bounded resource snapshot placed Core at about 13% of one logical CPU,
  Program confidence at 11%, Preview at 2%, multiview at 5%, and every other
  owned service below 2%; the largest owned RSS was about 246 MiB. The 4K
  encoder was idle because no stream key was entered during this rehearsal.
- `down --apply` removed all seven and only the seven rehearsal-owned
  containers. Both pre-existing ZoomISO instances remained running with zero
  restarts. The metered test VM was then verified `Stopped (deallocated)`.

The runtime design is cloud-provider neutral: it uses x86-64 Linux, Docker,
loopback services, and one host-local MXL shared-memory domain rather than an
Azure API. Equivalent non-burstable x86 instances on AWS or Google Cloud
should work when supplied with the same licensed images and capacity. Treat
that as portability guidance, not a provider acceptance claim, until the same
gate has passed on that provider. Keep all MXL media components on one VM;
shared memory does not span hosts. ARM instances are not currently qualified.

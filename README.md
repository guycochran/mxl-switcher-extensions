# MXL Switcher Extensions

Optional input, output, recording, and deployment integrations for
[`mxl-switcher-core`](https://github.com/guycochran/mxl-switcher-core).

For the guarded ZoomISO-to-Core-to-YouTube demonstration—including the
ATEM-style operator Skin and one encoded Program/Preview/multiview confidence
stack—start with the [end-to-end quickstart](QUICKSTART.md).
Operators beginning with a new host should first read the
[prerequisite onboarding guide](deploy/online-demo/PREREQUISITES.md),
which states exactly what this public repository can and cannot install.
AI-assisted operators should also read the repository's
[agent operator brief](AGENTS.md); it captures the safety boundary and proof
required without relying on private project history.

Core is useful without this repository. Install only the bolt-ons your workflow
requires.

## Choose a workflow

| Workflow | Profile | Typical components |
|---|---|---|
| Corporate/on-prem | [`profiles/corporate-on-prem`](profiles/corporate-on-prem) | SDI/HDMI, NDI, SRT, presentations, Companion |
| Online production | [`profiles/online-production`](profiles/online-production) | ZoomISO, meeting return, WebRTC, SRT, RTMPS |
| Recording/replay | [`profiles/archive-replay`](profiles/archive-replay) | File recording, TAMS, scrub, clips, replay |
| Complete demonstration | [`profiles/full-demo`](profiles/full-demo) | All validated demo components |
| Core developer | [`profiles/core-only`](profiles/core-only) | Core and diagnostic control only |

## Repository map

```text
common/                 shared lifecycle, health, and manifest conventions
inputs/                 transport or device -> stable MXL flow
outputs/                stable MXL Program Master -> destination
recording/              file and time-addressable recording/replay
profiles/               audience-specific deployment selections
schemas/                machine-readable extension contracts
docs/                   architecture and contribution guidance
```

## Maturity labels

Every package README and manifest uses one of these labels:

- `concept` — contract and intended behavior only;
- `prototype` — demonstrated code exists but is not packaged or accepted;
- `candidate` — installable and under staged acceptance;
- `accepted` — its documented profile passed the published acceptance gate.

An empty package directory does not claim implementation. Proprietary SDKs,
licensed applications, credentials, and hardware drivers are never vendored.

`scripts/check_profile.py` is the release truth check. It returns nonzero when
a profile uses a mutable Core ref, selects a package below candidate maturity,
or selects a package without an entrypoint. This lets the broad workflow
profiles remain useful roadmaps without accidentally presenting them as
reproducible releases.

## Architectural rule

Extensions are processes beside Core, never plugins inside its media hot path.
They communicate through stable MXL flows and bounded control/health contracts.
Stopping, restarting, or failing one extension must not interrupt Core or
another extension.

See [extension contract](docs/EXTENSION_CONTRACT.md) and
[broadcast-readiness plan](docs/BROADCAST_READINESS.md). The
[deployment architecture decision](docs/DEPLOYMENT_ARCHITECTURE.md) defines the
supported single-host default, the MXL 1.1 distributed preview, and the gates
required before promoting a multi-host profile. Also see
[Core's output-node architecture](https://github.com/guycochran/mxl-switcher-core/blob/main/docs/OUTPUT_ARCHITECTURE.md).

## Current starting point

This repository begins as a clean extraction boundary. The original MXL
Switcher proved that Program segments could be recorded and registered in a
TAMS store. It did **not** produce an accepted playback, scrubbing, trimming,
or replay workflow; those areas consumed substantial development time and
remain research. The new packages preserve the recording lessons while
replacing regenerated timestamps and implicit flow naming with the
clock-correct Program Master and explicit manifests from MXL Switcher Core.

The conventional [file recorder](recording/file-recorder) is now a prototype:
its supervised, bounded pipeline has passed a short isolated A/V mux and
finalization smoke. It remains below candidate maturity until stable switched
Program Audio, measured A/V skew/drop telemetry, and staged duration gates are
complete.

The [meeting-return output](outputs/meeting-return) now has a versioned concept
manifest and paired-clock acceptance contract. Its deterministic callback and
rate-controller simulation is complete, but real payload resampling and access
to the meeting runtime's internal device clock remain open. Its sender side has
passed an isolated hold, while the independent audience gate exposed
accumulating A/V error after the meeting boundary. It remains a concept until a
shared-clock adapter passes payload, local-loopback, and real audience gates.

The [NDI output](outputs/ndi) is now a paired Program A/V prototype extracted
from the accepted video-only Core NDI pilot. It supervises one bounded
GStreamer pipeline, publishes float32 48 kHz stereo audio with Program video,
reopens both readers together when either stable MXL flow is replaced, limits
restarts, and exposes the common loopback health contract. A live receiver
10-minute receiver gate proved 29.996 fps video and 48,002.7 Hz stereo audio
traverse the NDI wire without receiver error or sender restart. Readiness now
requires rate-qualified wire-level A/V proof,
preventing an incompatible, stalled, or presence-only path from appearing
healthy. The package remains below candidate maturity until content-level A/V
timing and flow-replacement recovery gates pass.

The [NDI Input Module](inputs/ndi) is now an independently supervised
prototype. It receives one NDI source per instance, normalizes cadence before
scaling and threaded v210 conversion, publishes optional Float32/48 kHz audio,
and exposes the common loopback health contract. Its offline pipeline,
validation, and bounded-restart tests pass. It remains below candidate maturity
until the timestamp A/B, two-camera moving-picture, recovery, audio, and staged
endurance gates in its delivery plan pass on real senders.

The [YouTube RTMPS output](outputs/rtmp) is a working 4K prototype with direct
four- or eight-source MXL composition, CPU and optional NVENC profiles, a
loopback-only operator window, in-memory stream-key handling, Program audio,
and bounded reconnect behavior. Its readiness signal now fails closed unless
every selected source, encoded video, audio, mux bitrate, and the eight-up
diagnostic clock are all advancing. A CPU-only cloud run delivered the complete
eight-source ZoomISO MXL composition, Program audio, and smoothly advancing
diagnostic timecode to the YouTube audience player at 4K. The documented live
sample used approximately 14.05–15.84 of the VM's 32 logical CPUs across
ZoomISO and the output adapter. This proves the end-to-end demonstration; the
sustained audience acceptance gate remains open pending paired timecode
captures and a continuously green telemetry window. The package is
deliberately still labeled `prototype`.

The follow-up controller build can derive one paired audio flow per selected
ZoomISO video source and mix all eight into the stereo Program bus. Its final
pre-shutdown state reported eight active video sources and eight active audio
sources. This expands the demonstrated single-source Program audio path, but
the new eight-source mix still needs a separate audience-listening gate before
it is described as accepted broadcast audio.

The shared operational layer is implemented today: manifests declare common
state/liveness/readiness paths, adapters can use the thread-safe lifecycle and
bounded restart primitives in `common/`, and `scripts/check_health.py` provides
a fail-closed one-shot promotion check. The reference Skin in Core can display
these independently supervised adapters on its EIC dashboard. This is plumbing
for reliable bolt-ons, not a claim that every transport pictured above is
already implemented.

License: MIT.

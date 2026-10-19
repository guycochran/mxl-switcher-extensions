# End-to-end ZoomISO MXL demonstration

**Release scope:** this is the supported technical-preview demonstration path,
not an unattended production appliance. Read the
[technical-preview boundary](docs/TECHNICAL_PREVIEW.md) for the exact evidence,
host assumptions, and open gates before making deployment claims.

This is the shortest supported path from a licensed ZoomISO Cloud meeting to:

- stable MXL Core Program and Preview outputs;
- an ATEM-style browser control surface;
- Program, Preview, and eight-source confidence multiview monitors; and
- a 4K CPU-only YouTube RTMPS controller with an in-memory stream key.

The launcher is intentionally split at the two steps only a human can safely
perform: authorizing ZoomISO and entering the YouTube stream key. It never
stores meeting credentials or streaming credentials.

## Before you begin

Use a Linux host with Docker, Git, Python 3, an operator-controlled MXL runtime
image,
and a running licensed ZoomISO Cloud container. The demonstrated eight-source
CPU profile used 32 modern logical CPUs and 128 GiB RAM. A four-source rehearsal
on a smaller host is unqualified; proceed only when the acceptance check and
resource sampling retain at least 20 percent whole-host CPU headroom. No GPU is
required for the proven CPU path.

ZoomISO Cloud is available from the vendor's public
[Liminal apps page](https://liminal.zoom.us/#apps). The prerequisite guide also
records the exact AMD64 version used by the accepted reference test.

The default image name `mxl-lab:latest` is a local lab convention, not a public
download. Before using another tag, read the exact
[prerequisite onboarding guide](deploy/online-demo/PREREQUISITES.md)
and [base-image contract](deploy/online-demo/BASE_IMAGE.md). The onboarding
guide defines what the operator must obtain and stage; `preflight` checks the
image contract inside the image before it permits a build or launch.
For cloud deployment, use the tested Azure reference or the Google Cloud and
AWS candidates in the [cross-cloud host sizing
guide](deploy/online-demo/CLOUD_SIZING.md).

## 1. Clone and initialize

```sh
git clone https://github.com/guycochran/mxl-switcher-extensions.git
cd mxl-switcher-extensions
./mxl-demo init
```

`init` detects usable Docker access, an unambiguous ZoomISO/MXL volume, and the
installed `mxl-lab:latest` operator-controlled base image, then writes the ignored local
`demo-config.json`. It refuses to guess when more than one likely volume exists
and prints the exact flag required. Examples:

```sh
./mxl-demo init --domain-volume VOLUME_NAME
./mxl-demo init --domain-path /absolute/mxl/domain
./mxl-demo init --source-count 4
./mxl-demo init --base-image YOUR_MXL_IMAGE
```

Use `--domain-path` when ZoomISO exposes the MXL domain as a host bind mount or
tmpfs rather than a named Docker volume. Select exactly one domain source.

The selected MXL domain must be readable by the account running the launcher.
If Docker stores it below a root-only directory, run the complete workflow
consistently with the appropriately privileged account. Running with `sudo`
changes `HOME`, so the ignored config and private `state_dir` will belong to
root unless you give `--config` an absolute path and set an explicit absolute
`state_dir`. Do not mix privileged and unprivileged lifecycle commands. The
generated file contains no credentials or flow UUIDs.

## 2. Prepare ZoomISO

Start ZoomISO Cloud, join the meeting, authorize capture, and map four or eight
numbered outputs as native MXL with matching audio. Wait until every mapped row
shows incoming video. This licensed, interactive step is deliberately not
automated. When **Capture** is enabled, ZoomISO Cloud automatically requests and
grants the recording permission needed for capture; the operator does not need
to grant that permission separately.

For a non-production source, the public
[Office Hours Zoom Test](https://officehours.global/zoomtest) page provides the
current link to its continuously operated test meeting. It is an external
service and may periodically reset, so confirm the meeting is active and all
mapped ZoomISO rows are advancing before launching the bundle. Do not use the
public meeting for confidential material.

## 3. Run one guarded launch

```sh
./mxl-demo preflight --config demo-config.json
./mxl-demo up --config demo-config.json --apply
```

`up` reruns the preflight, obtains the pinned Core revision, builds the required
local images, discovers only live paired ZoomISO outputs, writes private runtime
configuration, and starts the nine labelled services, including two warm
off-air/on-air layout banks. It stops on the first
failed prerequisite rather than leaving a partial deployment.

`plan` also performs fresh source discovery every time. It never reuses a
previous session's `resolved.json`, even if that file still exists.

Open both through an SSH tunnel rather than exposing them publicly:

```sh
ssh -N \
  -L 13010:127.0.0.1:9710 \
  -L 13089:127.0.0.1:8889 \
  -L 8189:127.0.0.1:8189 \
  -L 18123:127.0.0.1:18123 \
  USER@PRODUCTION_HOST
```

The left side of the WebRTC forward must match the port in
`confidence_browser_base`; its right side is `webrtc_port`. The ICE forward
must use `ice_tcp_port` on both sides because that port is advertised to the
browser. This separation lets the bundle coexist with an existing confidence
server without exposing either one.

- ATEM-style operator Skin: `http://127.0.0.1:13010/`
- EIC health: `http://127.0.0.1:13010/health`
- YouTube controller: `http://127.0.0.1:18123/`

Enter the RTMPS URL and stream key only in the YouTube controller. The key is
kept in memory, never returned by the API, and forgotten on stop.

## 4. Verify and clean up

```sh
./mxl-demo status --config demo-config.json
./mxl-demo check --config demo-config.json --seconds 30
./mxl-demo down --config demo-config.json --apply
```

`check` is the authoritative pass/fail gate. It requires owned services and
confidence pictures to advance without restarts, terminal torn reads, missing
sources, or Core hard-counter movement, and verifies YouTube source/audio
readiness. `down` removes only containers with the configured ownership label. It
does not stop ZoomISO, EasyMXL, Docker, the meeting, or unrelated services. If
the host is a metered cloud VM, deallocate it separately and verify the cloud
provider reports **deallocated**, not merely guest-stopped.

For the 30-minute promotion run, keep the SSH tunnel and YouTube output active
and leave the Skin's multiview visibly playing in the operator browser. Then
use the combined gate instead of running the Core and destination checks one
after another:

```sh
python3 scripts/qualify_online_program.py \
  --config demo-config.json \
  --url http://127.0.0.1:18123 \
  --seconds 1800
```

It continuously requires the same MediaMTX WebRTC multiview reader to remain
connected with advancing bytes and RTP packets and zero discarded frames. It
also samples all eight YouTube source branches, composition, audio,
encode, mux, destination bytes, reconnects, process resources, and whole-host
headroom while bracketing the same interval with the authoritative Core and
confidence checks. The private mode-0600 JSON report is written inside the
configured state directory. A machine pass still requires the operator to
confirm audible Program mix and acceptable audience A/V sync, and to capture
two audience frames at least ten seconds apart with different timecodes.

Read [`deploy/online-demo/README.md`](deploy/online-demo/README.md) for the
component boundaries, capacity guidance, failure behavior, and exact remaining
acceptance gates. That document also records the public-safe fresh-host smoke
result, including bounded findings that still require a longer acceptance run.
For a symptom-to-action guide, use
[`deploy/online-demo/TROUBLESHOOTING.md`](deploy/online-demo/TROUBLESHOOTING.md).
Before tagging a release candidate, run the
[public-instructions-only outsider rehearsal](deploy/online-demo/OUTSIDER_REHEARSAL.md).

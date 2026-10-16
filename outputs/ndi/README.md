# NDI output

Paired Program Master video/audio sent as one NDI source.

**Maturity: prototype.** The earlier Core NDI pilot accepted independent
video-only Program and Preview outputs under load. This package extracts that
flow-replacement behavior, adds the 48 kHz stereo Program audio branch, and
publishes the common extension health contract. A short live receiver smoke
has now proved simultaneous 1080p30 picture and 48 kHz stereo audio over the
wire. Duration, content-level A/V timing, recovery, and resource gates remain,
so the package is not marked accepted.

`ndi_output.py`:

- reads stable MXL Program video and audio flow identities;
- combines UYVY video and float32 48 kHz stereo audio with
  `ndisinkcombiner` before `ndisink`;
- uses bounded queues and argv-only process launch;
- requires both MXL essences and a successful NDI wire A/V receiver probe
  before becoming ready;
- detects a replaced MXL flow directory and reopens both readers together;
- limits restarts in a rolling window and fails closed when exhausted; and
- exposes loopback-only state, liveness, and readiness endpoints.

Example (the NDI runtime, GStreamer NDI plugin, and MXL plugin must already be
installed under their own licenses):

```sh
python3 outputs/ndi/ndi_output.py \
  --domain /dev/shm/mxl/domain \
  --video-flow PROGRAM_VIDEO_UUID \
  --audio-flow PROGRAM_AUDIO_UUID \
  --ndi-name "MXL Switcher Program" \
  --verification-source-name "SENDER-HOST (MXL Switcher Program)" \
  --port 18120
```

The verification name is the full source name shown to an NDI receiver, not
only the value passed to `--ndi-name`. Readiness remains red if the receiver
probe does not observe at least one video buffer and one audio buffer. The
probe is also available by itself:

```sh
python3 outputs/ndi/ndi_av_probe.py \
  --source-name "SENDER-HOST (MXL Switcher Program)" \
  --seconds 5
```

Run the adapter in a media image whose MXL reader is compatible with the MXL
writer that created Program. A process can otherwise remain alive while
receiving no grains. The required wire probe prevents that condition from
being reported as ready.

The adapter does not redistribute the proprietary NDI SDK/runtime. Before
promotion, qualify sustained picture/audio cadence, content-level A/V offset/drift,
flow replacement, receiver continuity, CPU/RSS, and restart behavior on the
target host.

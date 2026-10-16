# NDI output

Paired Program Master video/audio sent as one NDI source.

**Maturity: prototype.** The earlier Core NDI pilot accepted independent
video-only Program and Preview outputs under load. This package extracts that
flow-replacement behavior, adds the 48 kHz stereo Program audio branch, and
publishes the common extension health contract. The new paired A/V package has
not yet completed its own live qualification and is not marked accepted.

`ndi_output.py`:

- reads stable MXL Program video and audio flow identities;
- combines UYVY video and float32 48 kHz stereo audio with
  `ndisinkcombiner` before `ndisink`;
- uses bounded queues and argv-only process launch;
- requires both essences before becoming ready;
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
  --port 18120
```

The adapter does not redistribute the proprietary NDI SDK/runtime. Before
promotion, qualify discovery, picture, audio, content-level A/V offset/drift,
flow replacement, receiver continuity, CPU/RSS, and restart behavior on the
target host.

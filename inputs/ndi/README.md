# NDI Input Module

Receive one NDI source and publish stable video and optional audio as paired MXL
flows. One module instance owns one source; deploy additional independent
instances for additional cameras.

**Maturity: prototype.** The optimized pipeline and bounded supervisor have
offline coverage. Real-camera cadence, timestamp selection, recovery and
endurance qualification are still required before production use.

## Why this module exists

The original lab pipeline converted every decoded frame to v210 before
normalizing its frame rate. A 60 fps camera therefore paid for 60 expensive
conversions even when the production system needed 30 fps. Receive-time
timestamps also turned network arrival jitter into uneven MXL commits.

This module deliberately orders the video path as:

```text
NDI decode -> bounded queue -> videorate -> scale -> threaded v210 conversion -> MXL
```

The audio path drains NDI independently and normalizes it to interleaved
Float32 at 48 kHz. Audio may be required, optional, or disabled.

## Runtime boundary

The repository does **not** redistribute the proprietary NDI SDK or runtime.
Use an operator-controlled image that already contains the NDI and MXL
GStreamer elements. Build the small wrapper on top of that local image:

```sh
docker build --build-arg BASE_IMAGE=mxl-lab:latest \
  -t mxl-ndi-input:prototype \
  -f inputs/ndi/Dockerfile.runtime .
```

## Example

Use an isolated, writable MXL domain. Flow IDs must be unique to this module.

```sh
docker run --rm --network host \
  -v /dev/shm/mxl-ndi:/mxl-domain \
  mxl-ndi-input:prototype \
  --domain /mxl-domain \
  --source-name "CAMERA (Program)" \
  --video-flow 00000000-0000-4000-8000-000000000101 \
  --audio-flow 00000000-0000-4000-8000-000000000102 \
  --audio-mode optional \
  --width 1920 --height 1080 \
  --rate-numerator 30 --rate-denominator 1 \
  --timestamp-mode timestamp \
  --pacing-mode clock-sync \
  --port 18130
```

For direct connection, use `--source-address host:port` instead of
`--source-name`. The two selectors are intentionally mutually exclusive. NDI
Discovery Server configuration remains an NDI-runtime concern and can be
supplied through the runtime's normal NDI configuration directory.

Health endpoints bind to loopback by default:

- `GET /api/v1/state`
- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`

Readiness requires the media process to be alive and the configured video MXL
flow to have a valid definition plus recently written data. Required audio must
meet the same check; optional audio never blocks video readiness. The activity
window accounts for mmap writeback lag, but it does not prove moving-picture
cadence. Use the qualification gates in [PLAN.md](PLAN.md) before promotion.

## Timestamp selection

The module exposes `receive-time`, `timestamp`, `timecode`, and the NDI
plugin's clock-paced `clocked` mode. No mode is claimed as universally correct.
Run the one-camera A/B gate and select the mode that produces the lowest Core
held-frame ratio for the actual sender.

`--pacing-mode clock-sync` places GStreamer's clock synchronizer immediately
before the MXL writer so fixed-cadence buffers are committed on their PTS
schedule rather than in upstream arrival bursts. It is opt-in until qualified;
`none` preserves the unpaced path for comparison.

## Safety

- The module never clears an MXL domain or removes flow directories.
- It owns only the configured writer process and bounded restart budget.
- Health output omits source addresses, source names and flow UUIDs.
- No shell is used to launch the media pipeline.

## Tests

```sh
python3 -m unittest tests.test_ndi_input -v
make test
```

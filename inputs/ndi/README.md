# NDI Input Module

Receive NDI sources and publish stable video and optional audio as paired MXL
flows. For switching two or more cameras, use the **NDI Input Bank** so every
writer shares one GStreamer pipeline clock and one MXL clock-offset context.
Independent per-camera writer processes are not a supported switcher topology:
live qualification showed their MXL indexes can diverge even when each camera
is individually smooth.

**Maturity: prototype.** The optimized pipeline and bounded supervisor have
offline coverage. Real-camera cadence, timestamp selection, recovery and
endurance qualification are still required before production use.

## Why this module exists

The original lab pipeline converted every decoded frame to v210 before
normalizing its frame rate. A 60 fps camera therefore paid for 60 expensive
conversions even when the production system needed 30 fps. Receive-time
timestamps also turned network arrival jitter into uneven MXL commits.

The single-input path deliberately orders video as:

```text
NDI decode -> bounded queue -> videorate -> scale -> threaded v210 conversion -> MXL
```

The multi-input bank adds coordinated startup, a shared frame epoch, and a
per-input live frame synchronizer ahead of a post-conversion pacing reservoir.
The frame synchronizer emits fixed-cadence video and repeats the last picture
through short arrival stalls. Together those pieces turn independently timed,
bursty NDI receivers into clock-aligned MXL writers at the cost of bounded
latency, CPU and memory.

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

## Multi-input bank (recommended for switching)

Create the domain with at least one second of MXL history **before any MXL
process opens it**. The bank fails closed when this option is absent or too
small; the SDK's 200 ms default does not provide enough switcher history for
bursty contribution sources.

```sh
mkdir -p /dev/shm/mxl-ndi
printf '%s\n' \
  '{"urn:x-mxl:option:history_duration/v1.0":1000000000}' \
  > /dev/shm/mxl-ndi/options.json
```

Create a private `bank.json` (do not commit real camera addresses):

```json
{
  "sources": [
    {"id":"camera-a", "source_address":"camera-a.example:5961", "video_flow":"00000000-0000-4000-8000-000000000101", "audio_mode":"disabled"},
    {"id":"camera-b", "source_name":"CAMERA B", "video_flow":"00000000-0000-4000-8000-000000000201", "audio_mode":"disabled"}
  ]
}
```

Run all inputs in one container and pipeline:

```sh
docker run --rm --network host \
  -v /dev/shm/mxl-ndi:/mxl-domain \
  -v "$PWD/bank.json:/run/ndi-bank.json:ro" \
  --entrypoint python3 mxl-ndi-input:prototype \
  /opt/mxl-switcher-extensions/inputs/ndi/ndi_bank.py \
  --domain /mxl-domain --config /run/ndi-bank.json \
  --rate-mode normalize --timestamp-mode receive-time \
  --pacing-mode retimestamp-clock-sync --port 18130
```

`passthrough` fails closed unless every sender already matches the requested
geometry and frame rate. Use `normalize` for mixed senders, accepting its added
CPU cost. Bank mode deliberately prerolls converted frames before clock pacing;
budget roughly 0.7 seconds of contribution latency plus the downstream Core
input delay. The health API reports counts only; it never returns source names,
addresses, or flow IDs.

Each bank branch also has a 30-second media watchdog. A receiver that stops
delivering buffers terminates the shared pipeline, makes readiness fail, and
enters the supervisor's bounded restart policy instead of leaving a frozen
source falsely marked healthy.

On the older dual-Xeon qualification host, input heads committed about four
frames behind the current TAI index. A downstream Core delay of six frames
provided the measured safety margin. Treat that as a starting profile, not a
universal constant: qualify the head-to-TAI offset on each host and retain at
least two frames of additional margin inside the configured MXL history.

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

`--pacing-mode retimestamp-clock-sync` additionally replaces incoming PTS with
a deterministic timeline derived from the fixed-size v210 frame byte rate,
then paces that timeline. This mode is intended for contribution sources whose
timestamps cause MXL index skips or repeats; it must pass the live cadence gate
before becoming a deployment default.

## Safety

- The module never clears an MXL domain or removes flow directories.
- It owns only the configured writer process and bounded restart budget.
- Health output omits source addresses, source names and flow UUIDs.
- No shell is used to launch the media pipeline.

## Tests

```sh
python3 -m unittest tests.test_ndi_input tests.test_ndi_bank -v
make test
```

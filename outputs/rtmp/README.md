# RTMP/RTMPS output

Direct Program Master encode for services such as YouTube.

**Maturity: prototype.** The executable path reads MXL video and audio directly,
uses hardware H.264 encoding, and sets the RTMPS destination through the
GStreamer API after reading a mode-0600 secret file. The original Switcher's
RTSP-derived, timestamp-regenerating path is retained only as a lesson.

The direct eight-up mode builds a 3840x2160p30 3x3 canvas from eight MXL video
sources. Eight 1280x720 source tiles fill the first eight cells and the ninth is
a branded slate. The canvas feeds NVENC directly, avoiding an unnecessary 4K
uncompressed intermediate flow while remaining independent of Core.

## Boundary

The adapter reads stable Program video and Program audio directly from MXL. It
owns encoding, FLV muxing, RTMPS transport, retry policy, and destination
health. It never decodes the WebRTC confidence rendition and never adds codecs
or cloud credentials to Core.

A destination profile explicitly selects resolution, frame rate, H.264
bitrate/profile, GOP duration, AAC bitrate/sample rate, reconnect policy, and
the target service. The YouTube 4K prototype is 3840x2160p30, H.264 High at
30,000 Kbps CBR, a two-second GOP, two B-frames, and stereo AAC at 128 Kbps /
44.1 kHz. Service-specific requirements override only the adapter profile,
never Program Master.

## YouTube 4K proof

Create the key file on the media host without putting the key in shell history:

```bash
install -m 600 /dev/null "$HOME/.youtube-stream-key"
read -rsp 'YouTube stream key: ' key
printf '%s' "$key" > "$HOME/.youtube-stream-key"
unset key
```

Launch with exactly eight `--input 'LABEL=FLOW_UUID'` arguments. The canvas,
Program audio, encoder, mux, and secure transport are one supervised output:

```bash
python3 outputs/rtmp/rtmps_output.py \
  --domain /mxl-domain \
  --audio-flow "$PROGRAM_AUDIO_FLOW" \
  --stream-key-file "$HOME/.youtube-stream-key" \
  --input 'SOURCE 1=...' --input 'SOURCE 2=...' \
  --input 'SOURCE 3=...' --input 'SOURCE 4=...' \
  --input 'SOURCE 5=...' --input 'SOURCE 6=...' \
  --input 'SOURCE 7=...' --input 'SOURCE 8=...'
```

The command line contains only the secret-file path; the key is read and
applied inside the process.

Health is loopback-only on port 18120. A green readiness response requires
advancing encoded video, advancing encoded audio, and fresh mux output.

`Dockerfile.nvidia` layers the userspace CUDA/NVENC libraries that match the
Ubuntu 24.04 NVIDIA 580 guest driver onto the existing MXL media image. Run the
container with the NVIDIA character devices made available by the host; do not
use a privileged container.

## Secret contract

- The stream key is a secret reference, not ordinary configuration.
- Read it at runtime and set the sink location through the GStreamer API. It
  must never appear in argv, process listings, logs, health JSON, screenshots,
  evidence bundles, or Git.
- Public state reports only a redacted destination label and service type.
- Reject URLs containing userinfo, fragments, control characters, or an
  unapproved scheme. Production profiles require `rtmps://`.
- Bind health and control to loopback by default.

An argv-only `gst-launch` wrapper cannot meet this stream-key requirement and
is therefore not the production implementation.

## Readiness and telemetry

Process liveness or an open TCP socket is not readiness. Green requires:

- advancing encoded video and audio into `flvmux`;
- advancing bytes through the RTMPS sink;
- bounded encoder/mux queues;
- a connected destination with no active retry; and
- receiver- or platform-side stream health during promotion.

Publish encoder fps, audio sample rate, encoded bitrates, mux bytes, bytes
sent, queue depth, reconnects, current backoff, last successful write age,
CPU, and RSS. If a platform health API is configured, expose only its
non-secret health state and latency.

## Promotion sequence

1. Unit-test secret redaction, URL validation, queue bounds, GOP, bitrate,
   audio profile, and bounded retry behavior.
2. Run a local RTMP/RTMPS receiver and independently decode/count both
   essences. Presence alone is insufficient.
3. Prove Program flow replacement reopens video and audio together.
4. Run sustained clean-path cadence, content-level A/V offset/drift, and
   CPU/RSS gates.
5. Exercise disconnect/reconnect without duplicate writers or unbounded
   buffering.
6. Run the real service profile and verify audience-side picture, sound, and
   platform stream health.

Only after those gates pass should the extension move from `prototype` to
`candidate`.

# RTMP/RTMPS output

Direct Program Master encode for services such as YouTube.

**Maturity: concept.** Prototype lessons exist in the original MXL Switcher;
its RTSP-derived, timestamp-regenerating path will not be reused as the
production design. This package has no executable entrypoint today.

## Boundary

The adapter reads stable Program video and Program audio directly from MXL. It
owns encoding, FLV muxing, RTMPS transport, retry policy, and destination
health. It never decodes the WebRTC confidence rendition and never adds codecs
or cloud credentials to Core.

A destination profile explicitly selects resolution, frame rate, H.264
bitrate/profile, GOP duration, AAC bitrate/sample rate, reconnect policy, and
the target service. The initial general profile is 1080p30 H.264, a two-second
GOP, no B-frames, and stereo AAC at 48 kHz. Service-specific requirements
override only the adapter profile, never Program Master.

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

Only after those gates pass should `extension.json` move from `concept` or
gain an executable entrypoint.

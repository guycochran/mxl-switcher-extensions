# SRT output

Clock-paced MPEG-TS with encoded video and 48 kHz AAC audio, carried by SRT.

**Maturity: concept.** Hardware and WAN work in the original MXL Switcher
proved the transport lessons below, but this repository does not yet contain a
direct Program Master implementation. It must not be presented as available.

## Boundary

The adapter reads stable Program video and Program audio from MXL. It owns all
encoding, muxing, transport, reconnection, and destination health. Core remains
codec- and network-agnostic.

The first profile is deliberately narrow:

- progressive 1920x1080 at 30/1;
- H.264 with a two-second GOP and no B-frames;
- stereo AAC-LC at 48 kHz;
- MPEG-TS paced from Program timestamps;
- `mpegtsmux alignment=7`, producing seven 188-byte TS packets (1,316 bytes)
  per SRT payload; and
- SRT caller mode with latency chosen from measured RTT and jitter.

The original live implementation also found that an identity/pacing boundary
immediately after `mpegtsmux` was load-bearing. Preserve and retest that seam;
do not simplify it away from a pipeline string alone.

## Security contract

- A passphrase is optional, but if configured it is read from a secret
  reference and set through an in-process GStreamer property. It must never
  appear in argv, a URI, logs, health JSON, or a repository file.
- Reject passphrases outside SRT's 10-79 character range.
- Caller, listener, and rendezvous profiles are explicit; never infer a public
  listener from an ambiguous URI.
- Bind health and control to loopback by default.
- Reject destination URLs containing userinfo, fragments, or embedded secrets.

## Readiness and telemetry

Process liveness is not media readiness. A green adapter must demonstrate:

- advancing encoded video and audio into the mux;
- advancing MPEG-TS bytes into SRT;
- a connected destination or explicit listener peer;
- receiver-side decoded video cadence and audio sample rate during promotion;
- no continuity-counter errors during the gate; and
- bounded queues with no restart-budget exhaustion.

Publish at least RTT, retransmitted/lost packets, send-buffer occupancy, bytes
sent, reconnect count, video fps, audio sample rate, mux continuity errors,
encoder queue depth, CPU, and RSS. None of those fields may contain the
destination secret or complete private URI.

## Promotion sequence

1. Unit-test argv/URI validation, secret redaction, queue bounds, GOP and
   bitrate settings, 1,316-byte alignment, and restart-budget behavior.
2. Run a local listener/caller loopback with an independent receiver that
   decodes and counts both essences. Presence alone is insufficient.
3. Prove flow replacement reopens both Program readers together.
4. Run a sustained clean-network gate, then measured delay/loss/reconnect
   scenarios without loosening the clean-path cadence requirements.
5. Run the real destination profile and compare receiver timestamps and
   content-level A/V offset/drift.

Only after those gates pass should `extension.json` move from `concept` to
`prototype` or gain an executable entrypoint.

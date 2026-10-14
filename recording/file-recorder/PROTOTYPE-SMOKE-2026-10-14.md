# File recorder prototype smoke — 2026-10-14

This is an implementation smoke test, not an acceptance claim.

## Result

- The supervised recorder started, wrote media, stopped at its configured
  bound, finalized, and atomically promoted the output from `.partial.mkv` to
  `.mkv`.
- The finalized Matroska file was seekable and contained H.264 video at
  1920x1080 progressive 30 fps plus two-channel AAC at 48 kHz.
- The short media sample was 10.291 seconds and approximately 10.6 MB.
- The Core media process remained running. Invalid, early, late, timeout,
  reader-reopen, no-common-window, selected-input-regression, and sustained
  alarm counters all remained zero.
- The recorder's two independent read queues were bounded at two seconds.

## Important limitation

The test system did not currently publish a stable switched Program Audio
Master. This smoke therefore paired Program Video with the on-air source's
audio while that source remained pinned. It proves file creation, A/V muxing,
finalization, isolation from Core, and the target output caps. It does **not**
prove audio-follow-video across a cut, measured A/V skew, drop accounting, or
long-duration stability.

The next gate must first publish a stable Program Audio flow, then perform cuts
while recording and measure output duration, cadence, audio samples, A/V skew,
drops, CPU, RSS, disk rate, and finalization time.

## Follow-up with candidate Program Audio

A subsequent smoke paired stable Program Video with a newly published stable
48 kHz Program Audio candidate. Four AFV cuts completed while recording. The
recorder finalized a seekable Matroska file containing 1920x1080 progressive
30 fps H.264 and two-channel 48 kHz AAC; the file was approximately 19 MB with
20.455 seconds of media.

A separate concurrent probe showed zero Program Audio timestamp
discontinuities, and direct A/V readers measured timestamp proximity within one
30 fps frame. The supervisor ran for 30.4 seconds, so recorder startup/preroll
still needs to be separated from requested media duration. Drops, encoded-file
A/V skew, and content-level lip sync remain unmeasured. Recovered Core video
unavailability events during the repeated cuts also require analysis before a
clean acceptance claim.

## Real-time CPU profile follow-up

The original encoder unintentionally produced H.264 High 4:4:4 directly from
v210 and could not maintain real time on the loaded CPU-only host. The revised
profile explicitly converts to I420, uses the x264 ultrafast low-latency
profile, and parallelizes conversion and encoding. A 20.12-second capture then
produced 20.40 seconds of seekable 1920x1080p30 H.264 plus 48 kHz stereo AAC.

The recorder sampled at approximately 166% CPU and 95 MiB RSS. The host exposed
no VA-API, QSV, or NVENC device. Confining the recorder to four selected CPUs
with low cgroup scheduling weight preserved real-time output: 20.12 seconds of
capture produced 20.28 seconds of media. It reduced recovered Core alarm events
from three in an unconstrained sample to one, but did not eliminate them. No
alarm remained active and all hard Core read/timing counters stayed zero.

A no-recorder direct-cut control advanced every source and added zero alarm
events. The remaining event is therefore a co-location/isolation issue under
encode load, not a direct-cut correctness failure. This recorder stays at
prototype maturity; production should use a separately sized output node or a
hardware encoder unless a longer isolated gate passes cleanly.

No participant identity, face, meeting credential, private address, flow UUID,
host name, or raw operational log is included in this record.

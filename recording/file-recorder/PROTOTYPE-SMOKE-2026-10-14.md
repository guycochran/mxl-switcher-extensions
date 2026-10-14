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

No participant identity, face, meeting credential, private address, flow UUID,
host name, or raw operational log is included in this record.

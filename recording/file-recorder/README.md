# File recorder

Conventional clock-correct Program recording for operators who need a media
file rather than a time-addressable store.

**Maturity:** prototype.

The first implementation reads the paired Program Master video/audio flows,
keeps their MXL-derived GStreamer timestamps, creates H.264 plus 48 kHz AAC in
Matroska, and writes to a `.partial.mkv` path. A clean stop sends EOS through
GStreamer and only then atomically promotes the file to `.mkv`.

It is deliberately a process beside Core. A recorder failure cannot block or
restart the switcher. Both input queues are bounded at two seconds.

## Run

The host or container must provide GStreamer, `mxlsrc`, `x264enc`, `avenc_aac`,
and `matroskamux`, plus the MXL domain mount.

```bash
python3 recorder.py \
  --video-flow PROGRAM_VIDEO_UUID \
  --audio-flow PROGRAM_AUDIO_UUID \
  --recordings-dir /recordings \
  --max-duration-seconds 3600
```

Control is loopback-only by default:

```text
GET  /api/v1/state
GET  /api/v1/health
POST /api/v1/start
POST /api/v1/stop
```

The snapshot reports lifecycle state, wall-clock run duration, bytes written,
free disk, queue bound, and finalization. Media duration, `drops`, and
`av_skew_ns` are intentionally reported as `null`: those probes are required
before this can advance from prototype to candidate. The package makes no
acceptance claim yet.

## Gate to candidate

1. Verify both flow identities against the facility manifest.
2. Record a 10-minute Program with moving picture and continuous tone/speech.
3. Inspect the finalized file for 1920x1080 cadence, 48 kHz stereo audio,
   monotonic timestamps, duration agreement, and bounded A/V skew.
4. Repeat for 30 minutes, then eight hours while capturing CPU, RSS, disk rate,
   source discontinuities, drops, and finalization time.
5. Prove that stopping or killing the recorder does not move Core counters or
   interrupt Program.

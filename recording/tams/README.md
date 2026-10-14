# TAMS recorder and replay

Optional time-addressable recording, live scrub, clipping, and MXL replay.

**Maturity:** recording prototype only. The original MXL Switcher recorded and
registered segments, but playback, scrubbing, trimming, clip export, and replay
never reached a dependable result. This package does not claim those functions
as implemented or validated.

## Evidence boundary

What the original work established:

- Program media could be segmented and uploaded continuously;
- segments could be registered against time ranges;
- bounded spooling, parallel upload, pruning, and thumbnail generation were
  useful operational patterns.

What it did **not** establish:

- smooth or dependable long-running playback;
- accurate, repeatable trimming;
- robust live-edge and archive-window behavior;
- frame-accurate clip export;
- replay back into the switcher.

The unproven functions must be developed behind small disposable experiments.
They may not hold up recorder acceptance or be advertised in a demo until each
has its own gate.

## Target vertical slices

The work is intentionally split so successful recording is never held hostage
by a difficult player or editor.

### A. Recorder

1. Read clock-correct Program Master video and 48 kHz audio directly from MXL.
2. Produce independently decodable one-second H.264/AAC segments.
3. Preserve the Program epoch in TAMS Flow Segment timeranges.
4. Upload media directly to object storage and register it through the TAMS API.
5. Report live-edge lag, gaps, spool depth, bytes, storage use, and restarts.

### B. Reader

6. Prove bounded-window playback independently of the production Skin.
7. Verify seeking at the live edge, across gaps, and over a long-running flow.

### C. Trim and export

8. Prove timerange selection against known burned-in timecode.
9. Export a deterministic clip without changing the underlying recording.

### D. Replay

10. Load an accepted timerange into a separately supervised playout adapter.
11. Publish replay as a stable MXL source for Preview and Cut.

## Lessons retained from the original implementation

- upload media over the fastest storage path;
- use bounded parallel registration and ignore files still being written;
- keep the outage spool bounded;
- prune media, thumbnails, storyboards, and expired clip references together;
- keep player windows bounded instead of loading an entire long-running archive;
- use nearest-available thumbnail behavior near the live edge;
- do not treat a reverse-list query as proof of the newest registered segment;
- separate proxy-quality scrub media from full-quality export media when needed.

## Required work before candidate status

- adapt the client to the selected current TAMS service/API version;
- remove embedded infrastructure and credential assumptions;
- replace filename-derived timing with Program Master timing metadata;
- package the recorder, store dependencies, browser, and playout separately;
- add synthetic drift/gap tests plus 10-minute and 30-minute live gates;
- prove recorder and playout restarts cannot disturb Core.

Recorder candidate status does not imply reader, trim, export, or replay
candidate status. Each advances independently.

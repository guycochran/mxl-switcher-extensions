# TAMS recorder and replay

Optional time-addressable recording, live scrub, clipping, and MXL replay.

**Maturity:** prototype extraction. The original MXL Switcher demonstrated the
workflow; this package does not yet claim a packaged or accepted implementation.

## Target vertical slice

1. Read clock-correct Program Master video and 48 kHz audio directly from MXL.
2. Produce independently decodable one-second H.264/AAC segments.
3. Preserve the Program epoch in TAMS Flow Segment timeranges.
4. Upload media directly to object storage and register it through the TAMS API.
5. Report live-edge lag, gaps, spool depth, bytes, storage use, and restarts.
6. Browse the growing archive with storyboard thumbnails.
7. Mark or quick-grab a timerange and load it into a TAMS playout adapter.
8. Publish replay as a stable MXL source for Preview and Cut.

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

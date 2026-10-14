# Archive/replay profile

Adds recording to an existing Core deployment without changing Core.

## Default selection

- Program file recorder
- optional TAMS recorder and object store
- live/archive scrub interface
- clip bin and export
- TAMS playout adapter publishing an MXL replay source
- Skin and Companion record/replay controls
- recorder health on the EIC dashboard

The first accepted gate covers Program recording only. Playback is a second
gate, trimming/export a third, and MXL replay a fourth. A failure in one later
stage does not invalidate or delay the recorder. ISO recording becomes a
manifest selection only after the Program recorder is stable.

**Status:** profile definition; TAMS extraction is prototype maturity.

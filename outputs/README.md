# Output extensions

Each output extension independently reads the paired, clock-correct Program
Master flows. No destination clock, codec, reconnect, or driver may backpressure
Core or another output.

Every listed destination has a machine-readable concept manifest, including
its paired Program roles, external dependencies, secret references, and common
health contract. A concept manifest is a compatibility target—not an
implementation or acceptance claim. Meeting return remains first in the
promotion order, followed by NDI, SRT, RTMPS, and DeckLink.

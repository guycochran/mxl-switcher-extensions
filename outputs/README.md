# Output extensions

Each output extension independently reads the paired, clock-correct Program
Master flows. No destination clock, codec, reconnect, or driver may backpressure
Core or another output.

Every listed destination has a machine-readable manifest, including its paired
Program roles, external dependencies, secret references, and common health
contract. A concept manifest is a compatibility target—not an implementation
or acceptance claim.

Current promotion order:

1. finish the paired NDI duration, content-timing, and recovery gates (its
   short full-rate wire smoke has passed);
2. qualify the meeting-return payload resampler, then repeat local-loopback
   and real-audience gates once the device-clock boundary is observable;
3. implement SRT with 1,316-byte MPEG-TS alignment and receiver-proven A/V;
4. implement secure RTMPS without exposing stream keys in argv or logs; and
5. qualify scheduled DeckLink playback and the remaining destination profiles.

Each output advances independently. A blocked meeting-return dependency does
not prevent NDI, SRT, recording, or other bolt-ons from progressing.

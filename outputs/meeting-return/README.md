# Meeting-return output

Expose the paired Program Master as the camera and microphone sent into a
destination meeting. The adapter is outside Switcher Core and may be replaced
for a different meeting platform without changing Program or Preview.

**Maturity: concept.** The MXL sender side has passed a ten-minute CPU-only
qualification, but independent audience-side measurement found accumulating
A/V error after the meeting boundary. The package is not an accepted output.

## Boundary

```text
Core + Program Audio -> paired Program Master -> meeting-return adapter
                                                   | virtual camera
                                                   | virtual microphone
                                                   v
                                            destination meeting
                                                   v
                                         independent observer
```

The adapter reads stable `program.video` and `program.audio` MXL roles. It does
not own switching, layouts, source lifecycle, or the Core routing loop. A
meeting disconnect, SDK fault, queue overflow, or adapter restart must not
change the upstream Program identities or interrupt another output.

## Required timing behavior

The adapter must not run the virtual camera and virtual microphone as unrelated
best-effort consumers. It owns one monotonic output clock and maps both callback
domains to that clock.

- Video and audio start from one declared epoch.
- Both queues are bounded; latency cannot grow without limit.
- Audio correction and video drop/repeat policies are explicit and counted.
- A writer re-anchor or timestamp discontinuity either recovers within the
  declared bound or fails closed.
- A fixed presentation offset may align a stable path, but it is never used to
  conceal measured drift.
- Wall-clock timestamp regeneration is not the synchronization strategy.

## Runtime state

In addition to the common extension state, the adapter reports:

- camera and microphone enable/mute state;
- meeting connection and participant-send state;
- video/audio callback rate and worst callback interval;
- video/audio queue depth, high-water mark, underruns, and overruns;
- input and output rate error in parts per million;
- video drops/repeats and audio inserted/dropped samples;
- PTS discontinuities, re-anchors, and hard corrections;
- current and worst A/V skew; and
- bounded restart count plus the last error.

`ready=true` means the paired Program source is available and both output
devices can accept scheduled media. `running` means the destination meeting is
actively receiving both. A connection with only one essence is `degraded`, not
healthy.

## Current evidence boundary

Objective reference media was sample-exact immediately before the meeting
adapter, and Core counters stayed clean with ample host resources. A clean
48 kHz virtual-microphone swap removed an earlier SDK send error, but the
audience observer still measured growing audio lag and reduced unique-video
cadence. An audio-only PLL stayed internally stable and did not repair the
paired return. This localizes the next work to paired callback scheduling; it
does not justify an arbitrary audio delay.

## Promotion ladder

1. Deterministic callback-jitter simulation with positive/negative rate error,
   burst delivery, stalls, re-anchors, and bounded queues.
2. Local loopback through the actual virtual camera/microphone boundary.
3. Independent audience capture in a real destination meeting.
4. Unchanged ten-minute, thirty-minute, and eight-hour holds.

The audience capture must use moving frame codes and audio events tied to those
codes. Acceptance requires bounded content-level A/V offset and range, zero
unexplained discontinuities, declared cadence/correction limits, zero process
restarts, and no upstream Core counter movement.

The entrypoint remains intentionally absent until the shared-clock scheduler
passes the offline gate. Licensed meeting SDKs and credentials are installed
and referenced externally; they are never vendored or committed here.

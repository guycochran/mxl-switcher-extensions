# DeckLink SDI/HDMI output

Scheduled video and embedded 48 kHz audio playback using the hardware reference
clock.

**Maturity: concept.** Blackmagic Desktop Video is installed separately under
its own licence. This repository does not yet contain a DeckLink entrypoint and
does not claim hardware acceptance.

## Boundary

The adapter reads stable Program video and Program audio from MXL and owns the
device handle, mode negotiation, preroll, scheduled playback, embedded audio,
reference status, underflow recovery, and health. Core never opens DeckLink or
waits on an SDI/HDMI device.

The first profile is intentionally constrained:

- one explicitly selected DeckLink device and connector;
- one explicitly selected progressive output mode;
- v210 where the device/mode accepts it, otherwise one measured UYVY
  conversion at the adapter boundary;
- stereo 48 kHz PCM embedded in the output; and
- scheduled video/audio playback against the DeckLink hardware clock.

Immediate best-effort frame writes are not an acceptable production path.

## Safety and ownership

- Refuse an ambiguous device name or mode; never choose the first card.
- Open the device exclusively and fail if another process owns it.
- Require an operator-selected reference policy: free-run, external reference,
  or fail-closed when genlock is absent.
- Keep all device and mode identifiers outside Core configuration.
- A stopped or missing Program flow must produce a documented hold/black and
  silence policy without destabilizing Core.

## Readiness and telemetry

Green requires successful mode negotiation, completed A/V preroll, scheduled
playback advancing on the hardware clock, and no active underflow. Publish:

- selected device, connector, pixel format, frame rate, and audio channels;
- hardware reference/genlock state;
- buffered and scheduled video frames;
- buffered and scheduled audio sample frames;
- completed-frame timestamps and lateness;
- dropped/late frames, audio underruns, device resets, and restarts; and
- callback age, CPU, and RSS.

## Promotion sequence

1. Unit-test exact device/mode selection, schedule arithmetic, preroll bounds,
   and underflow/restart behavior with a fake DeckLink boundary.
2. Run a card loopback with moving timecode plus deterministic audio markers.
3. Measure content-level A/V offset/drift and frame cadence in free-run mode.
4. Repeat with external reference where supported and prove reference-loss
   policy.
5. Exercise Program flow replacement and cable/device interruption without
   duplicate ownership or unbounded buffering.
6. Run a sustained hardware gate with CPU/RSS and thermal observations.

Only after those gates pass should `extension.json` move from `concept` or
gain an executable entrypoint.

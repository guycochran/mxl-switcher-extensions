# NDI Input Module delivery plan

## Contract

One independently supervised module receives one NDI source and publishes one
stable v210 MXL video flow plus an optional Float32/48 kHz MXL audio flow.
Multiple inputs are multiple module instances. A failed source must not restart
another source, Core, Program or Preview.

The production-normalized profile is 1920x1080 at 30/1. A later native profile
may preserve source raster and cadence, but it is not the Office Hours default.

## Performance budget

For each 1080p30 input on the reference Supermicro:

- target no more than 1.5 CPU cores and 250 MiB steady RSS;
- retain at least 20 percent whole-host idle with two inputs and Core;
- deliver 28.5-30 genuinely new pictures per second;
- keep Core `held / routed` at or below 5 percent; and
- produce no Core hard-counter movement or progressive RSS growth.

The previous 1.7-2.2-core ingress cost and 46 percent held-frame result are the
baseline to beat, not acceptance values.

## Delivery stages

### 1. Pipeline and timestamp A/B

Use one camera with obvious motion and a moving time reference. Compare the old
and optimized pipeline orders across `receive-time`, `timestamp`, and
`timecode`. Record source rate, committed MXL rate, Core held/routed ratio,
queue loss, CPU and RSS. Select the default only from evidence.

### 2. Two-input smoke

Run two independent 1080p30 modules for 120 seconds with NDI returns,
autoframing and continuous picture probes disabled. Exercise Preview, Cut and
Take. Require visible motion on both sources and every performance-budget
condition above.

### 3. Lifecycle and recovery

Independently exercise sender stop/start, discovery loss, direct-address
reconnect, module restart and Core restart. The configured MXL identities remain
stable and unrelated services do not restart. Restart exhaustion fails closed.

### 4. Audio

Verify absent audio, and 1/2/4/8-channel sources. Measure 48 kHz continuity,
inserted/dropped samples, drift, recovery and audio-follow-video behavior.

### 5. Endurance

Run 10-minute, 30-minute and eight-hour gates in that order. Preserve exact
software revisions, source formats, CPU/RSS ranges, flow and Core counters,
reconnects and cleanup evidence.

## Control-plane follow-up

After the media gates pass, add an operator surface with one card per module:
source selection, direct address, detected and target formats, lifecycle state,
input/output cadence, queue drops, reconnects, CPU/RSS and recent errors. Do not
mount an unrestricted Docker socket; lifecycle control must be narrowly scoped.

## Escalation decision

Continue with the GStreamer implementation if it meets the cadence and resource
budget. Build a direct NDI SDK-to-libmxl implementation only if measurements
show that GStreamer conversion, timestamp control or recovery prevents
acceptance.

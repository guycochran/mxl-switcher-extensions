# NDI Input Module delivery plan

## Contract

One independently supervised **NDI Input Bank** receives two to eight NDI
sources and publishes one stable v210 MXL video flow per source. One native
coordinator owns every MXL writer and commits all video flows to the same
explicit grain index on each production tick. A failed source fails the bank
closed, without restarting Core, Program, Preview, or unrelated services.

The native bank is video-only today. Float32/48 kHz audio remains on the
single-input GStreamer path until the coordinated native audio writer has its
own continuity and recovery evidence.

The production-normalized profile is 1920x1080 at 30/1. A later native profile
may preserve source raster and cadence, but it is not the Office Hours default.

## Performance budget

For each 1080p30 input on the reference Supermicro:

- target no more than 1.5 CPU cores and 250 MiB steady RSS;
- retain at least 20 percent whole-host idle with two inputs and Core;
- deliver 28.5-30 genuinely new pictures per second;
- keep Core `held / routed` at or below 5 percent; and
- produce no Core hard-counter movement or progressive RSS growth.

The previous independent/GStreamer writer experiments are comparison evidence,
not acceptance values. They produced 27-35 percent held frames and as much as
28 frames of cross-source skew despite zero hard MXL errors. The first native
shared-index smoke reduced skew to 0-1 frame. Core then exposed a separate
single-thread memory-copy bottleneck; refreshing its freeze-frame cache once
per second instead of on every routed frame reduced a subsequent real-camera
smoke to 2.1 percent held frames with zero skips and zero hard errors.

## Delivery stages

### 1. Pipeline and timestamp A/B

Use one camera with obvious motion and a moving time reference. Compare the old
and optimized pipeline orders across `receive-time`, `timestamp`, and
`timecode`. Record source rate, committed MXL rate, Core held/routed ratio,
queue loss, CPU and RSS. Select the default only from evidence.

### 2. Two-input smoke

Run one two-source 1080p30 bank for 120 seconds with NDI returns,
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

## Architecture decision

The escalation threshold was reached. GStreamer remains responsible for NDI
receive, cadence normalization, scaling, and v210 conversion, but `mxlsink` is
no longer the bank writer. The native coordinator drains the latest converted
frame from each receiver and uses the public libmxl C API to open and commit the
same explicit MXL grain index for every flow. This preserves the portable NDI
plugin boundary while making multi-flow cadence deterministic.

Do not return to independent `mxlsink` tuning unless new evidence disproves the
shared-index result. The remaining qualification work is lifecycle/recovery,
native audio, and 10-minute/30-minute/eight-hour endurance in that order.

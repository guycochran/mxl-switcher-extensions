# MXL layout compositor

This prototype turns two through eight existing MXL video flows
into one paced 1920×1080 MXL scene flow. It is the portable scene engine behind
the operator Skin's ME controls.

The online demo runs two banks. The Skin always rebuilds the bank that is not
on Program, routes the completed bank to Preview, and changes Program only when
the operator presses Take. A failed build therefore leaves the live output
unchanged.

## Presets

- 2-up: equal side-by-side interview
- 3-up: one large speaker with two stacked guests
- 4-up: 2×2 grid
- 5-up: balanced 3-over-2 grid
- 6-up: 3×2 grid
- 7-up: balanced 4-over-3 grid
- 8-up: 4×2 wall

The compositor scales with padding rather than cropping source video.

## Runtime contract

Each bank exposes only loopback HTTP:

- `GET /api/v1/state`
- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `POST /api/v1/scene` with `{"count":3,"sources":[1,2,3]}`

The bank replaces only its compositor child when its off-air scene changes.
The output flow identity remains stable so Core can keep the bank registered as
`supersource-a` or `supersource-b`.

The default pixel path uses the same explicit fast-scaling shape proven by the
4K YouTube canvas: each selected v210 source is scaled and converted to its BGRA
tile, the tiles are composed once, and the result is converted back to v210 for
MXL. `--pixel-path direct-v210` remains available for bounded comparison, but it
is not the production default.

Readiness is cadence-qualified, not presence-only. The default requires every
selected input to advance and the composed output to average at least 27 fps
over a two-second window. A resource-starved host therefore refuses the scene
instead of placing a stuttering layout in Preview.

Use the guarded `deploy/online-demo/mxl_demo.py` lifecycle rather than launching
these files by hand. The launcher discovers current sources, generates stable
deployment-local bank flow IDs, owns both bank containers, enables the Skin
helper, verifies readiness, and removes only its labelled containers.

## Current acceptance boundary

The pure layout, pipeline, loopback-client, deployment-plan, validation, and
safe-bank tests run offline. A real-source functional gate on a legacy CPU host
proved that the original direct-v210 presets build and publish stable MXL
output, but its four-core profile fell from about 11 fps at 3-up to about 4 fps
at 8-up. That host/profile remains a documented no-go.

An isolated moving-reference gate on a modern 32-vCPU host compared the two
pixel paths at identical 1080p30, eight-reader, eight-core limits. Direct v210
held 29.99 fps at about 4.49 CPU cores and 203 MiB RSS. The corrected BGRA path
held 29.99 fps at about 2.37 cores and 206 MiB RSS, reducing measured CPU by
about 47%. With BGRA limited to four cores, every preset from 2-up through 8-up
held 29.99 fps; measured use ranged from about 1.62 to 3.03 cores. Two
simultaneous warm 8-up banks also held 29.99 fps each under their four-core
limits.

The follow-up live qualification used eight distinct ZoomISO MXL video flows on
the same CPU-only reference host. Every preset from 2-up through 7-up held
approximately 29.6--29.8 fps. An initial 8-up attempt exposed a cold/transient
pause, so the decisive runs excluded the first ten seconds rather than hiding
it in a whole-run average. The four-core 8-up bank then sustained 29.985 fps;
raising its limit to six cores produced essentially the same 29.967 fps. Two
four-core 8-up banks running together sustained 29.32 and 29.74 fps, with all
eight inputs advancing in both banks. The banks generally used about 3.1--3.6
logical CPUs and roughly 0.41--0.44 GiB RSS apiece during that dual-bank run.

A separate guarded 30-second Core acceptance window against the same eight
live source pairs passed: Program, Preview, and the eight-source confidence
wall all advanced; no owned service restarted; all Core hard counters stayed
zero and unchanged; and the YouTube controller detected eight video plus eight
audio sources ready for an operator destination. These are bounded live-source
and control-path results. They do not replace an audience-observed long-run A/V
sync or transmitted-program quality gate.

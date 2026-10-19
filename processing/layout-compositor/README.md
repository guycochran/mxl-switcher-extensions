# MXL layout compositor

This prototype turns two, three, four, six, or eight existing MXL video flows
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

Five- and seven-box scenes are intentionally absent until they have explicit
designs. The compositor scales with padding rather than cropping source video.

## Runtime contract

Each bank exposes only loopback HTTP:

- `GET /api/v1/state`
- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `POST /api/v1/scene` with `{"count":3,"sources":[1,2,3]}`

The bank replaces only its compositor child when its off-air scene changes.
The output flow identity remains stable so Core can keep the bank registered as
`supersource-a` or `supersource-b`.

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
proved that all five presets build and publish stable MXL output, with scene
replacement readiness in 0.7–1.8 seconds. The same run also proved that the
four-core cap is insufficient for larger layouts: measured output declined
from about 11 fps at 3-up to about 4 fps at 8-up while the container saturated
its allowance. That host/profile is a documented no-go. A modern CPU gate must
still demonstrate every preset at target cadence with zero Core hard-counter
movement before this extension moves beyond prototype maturity.

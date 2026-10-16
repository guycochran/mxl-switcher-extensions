# Extension contract

## Boundary

An extension is a separately supervised process that reads or publishes MXL
flows. Core does not load extension code, transport SDKs, codecs, or hardware
drivers.

Input extensions translate an external source into a stable, facility-profiled
MXL flow. Output extensions read the paired Program Master flows and translate
them to a destination. Recorder extensions are outputs; replay extensions are
inputs.

## Required manifest fields

Every package contains `extension.json`, validated by
[`schemas/extension.schema.json`](../schemas/extension.schema.json). It declares
identity, maturity, compatible Core API, flow roles, capabilities, entrypoint,
health paths, and any external dependencies. Health binds are loopback-only;
an authenticated facility proxy may expose them deliberately.

## Runtime state

All implementations expose a common snapshot, directly or through a supervisor:

```json
{
  "name": "program-tams",
  "kind": "recorder",
  "state": "running",
  "ready": true,
  "revision": 42,
  "restarts": 0,
  "last_error": null,
  "metrics": {}
}
```

Allowed states are `disabled`, `starting`, `ready`, `running`, `degraded`, and
`failed`. Readiness means media can be accepted or delivered; liveness means
only that the process responds.

Every manifest declares the common state, liveness, and readiness paths. The
current v1 contract uses `/api/v1/state`, `/api/v1/health/live`, and
`/api/v1/health/ready`. A liveness response is not evidence that an adapter is
safe to route; automation and EIC surfaces use readiness for that decision.

## Media rules

- Pair video and audio by manifest identity, never by matching labels.
- Preserve the Program Master epoch and deliberately map it to the destination
  timebase.
- Do not use regenerated wall-clock PTS as the primary synchronization method.
- Bound every queue and report its occupancy.
- A reconnect cannot backpressure Core or grow latency without bound.
- External health readers must not create backpressure in small MXL rings.

## Acceptance

Each implementation progresses through synthetic, local-loopback, real
destination, 10-minute, 30-minute, and eight-hour gates as applicable. Its
README names the exact accepted profile. Other resolutions, frame rates,
channels, hardware, or destinations remain unclaimed until separately tested.

## Secrets and licensed dependencies

Manifests may name `secret_ref` values but never contain secrets. Licensed SDKs
and applications are installed by the operator and are not committed or
redistributed by this repository.

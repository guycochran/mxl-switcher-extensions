# Common extension runtime

`runtime.py` provides the transport-neutral pieces shared by every adapter:

- thread-safe, revisioned lifecycle state;
- readiness separate from process liveness;
- atomic JSON-safe health metrics;
- rejection of secret-like metric fields; and
- a deterministic sliding-window restart budget.

It never launches, stops, or sleeps for a media process. Each adapter owns its
media pipeline and calls the common runtime at explicit lifecycle boundaries.
This keeps restart decisions observable and testable instead of hiding them in
an unbounded supervisor loop.

`health.py` gives adapters one loopback-friendly HTTP contract:

- `GET /api/v1/state` returns the complete public snapshot;
- `GET /api/v1/health/live` confirms the control process can answer;
- `GET /api/v1/health/ready` returns 200 only when media is ready, otherwise
  503; and
- `GET /api/v1/health` is the readiness-compatible shorthand.

Keeping liveness and readiness separate prevents a running-but-unusable media
adapter from appearing healthy on an EIC dashboard.

**Maturity:** prototype. The API is covered by offline tests and the file
recorder is the first integrated adapter; it is not yet a separately versioned
package.

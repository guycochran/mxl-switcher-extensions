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

**Maturity:** prototype. The API is covered by offline tests and is ready for
the first output-adapter integration; it is not yet a separately versioned
package.

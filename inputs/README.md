# Input extensions

Input modules translate a transport, meeting participant, or capture device
into stable paired MXL source roles. Their manifests state implementation
maturity, dependencies, published roles, and the common health contract without
claiming acceptance prematurely. Stable identities survive a publisher restart;
discovery and reconnection must not require changing Core's source ID.

Each input extension converts one transport or device into a stable,
facility-profiled MXL flow. Transport reconnects and malformed external media
remain outside Core.

The [NDI Input Module](ndi) is the first implemented transport input prototype.
It normalizes cadence before expensive v210 conversion and supervises one NDI
source without controlling Core or another input.

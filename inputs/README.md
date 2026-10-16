# Input extensions

Input bolt-ons translate a transport, meeting participant, or capture device
into stable paired MXL source roles. The concept manifests name dependencies,
secrets, published roles, and the common health contract without claiming that
the adapter is implemented or accepted. Stable identities survive a publisher
restart; discovery and reconnection must not require changing Core's source ID.

Each input extension converts one transport or device into a stable,
facility-profiled MXL flow. Transport reconnects and malformed external media
remain outside Core.

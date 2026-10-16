# End-to-end ZoomISO MXL demonstration

This is the shortest supported path from a licensed ZoomISO Cloud meeting to:

- stable MXL Core Program and Preview outputs;
- an ATEM-style browser control surface;
- Program, Preview, and eight-source confidence multiview monitors; and
- a 4K CPU-only YouTube RTMPS controller with an in-memory stream key.

The launcher is intentionally split at the two steps only a human can safely
perform: authorizing ZoomISO and entering the YouTube stream key. It never
stores meeting credentials or streaming credentials.

## 1. Clone and configure

```sh
git clone https://github.com/guycochran/mxl-switcher-extensions.git
cd mxl-switcher-extensions
cp deploy/online-demo/demo-config.example.json demo-config.json
```

Edit `demo-config.json` for the local MXL domain and licensed base image. Keep
the file outside Git if it contains facility-specific paths; the repository
ignores this exact filename. `domain_host_path` must be readable by the account
running the launcher. If Docker keeps the volume below a root-only directory,
run the launcher consistently with the same appropriately privileged account
or bind the domain at a readable host path. The example contains no credentials
or flow UUIDs.

## 2. Prepare ZoomISO

Start ZoomISO Cloud, join the meeting, authorize capture, and map four or eight
numbered outputs as native MXL with matching audio. Wait until every mapped row
shows incoming video. This licensed, interactive step is deliberately not
automated.

## 3. Run one guarded launch

```sh
python3 deploy/online-demo/mxl_demo.py doctor --config demo-config.json
python3 deploy/online-demo/mxl_demo.py bootstrap --config demo-config.json --apply
python3 deploy/online-demo/mxl_demo.py launch --config demo-config.json --apply
```

`bootstrap` obtains the pinned Core revision when needed and builds three local
images. `launch` discovers only live numbered ZoomISO MXL outputs, writes a
private runtime configuration, starts Core, starts one encoded confidence copy
for Program/Preview/multiview, starts the ATEM-style Skin on loopback port 9710,
and starts the YouTube controller on loopback port 18123.

Open both through an SSH tunnel rather than exposing them publicly:

```sh
ssh -N \
  -L 13010:127.0.0.1:9710 \
  -L 13089:127.0.0.1:8889 \
  -L 8189:127.0.0.1:8189 \
  -L 18123:127.0.0.1:18123 \
  USER@PRODUCTION_HOST
```

The left side of the WebRTC forward must match the port in
`confidence_browser_base`; its right side is `webrtc_port`. The ICE forward
must use `ice_tcp_port` on both sides because that port is advertised to the
browser. This separation lets the bundle coexist with an existing confidence
server without exposing either one.

- ATEM-style operator Skin: `http://127.0.0.1:13010/`
- EIC health: `http://127.0.0.1:13010/health`
- YouTube controller: `http://127.0.0.1:18123/`

Enter the RTMPS URL and stream key only in the YouTube controller. The key is
kept in memory, never returned by the API, and forgotten on stop.

## 4. Verify and clean up

```sh
python3 deploy/online-demo/mxl_demo.py status --config demo-config.json
python3 deploy/online-demo/mxl_demo.py stop --config demo-config.json --apply
```

The stop command removes only containers with the configured demo prefix. It
does not stop ZoomISO, EasyMXL, Docker, the meeting, or unrelated services. If
the host is a metered cloud VM, deallocate it separately and verify the cloud
provider reports **deallocated**, not merely guest-stopped.

Read [`deploy/online-demo/README.md`](deploy/online-demo/README.md) for the
component boundaries, capacity guidance, failure behavior, and exact remaining
acceptance gates.

# Online demo troubleshooting

Use the first matching symptom below. Do not stop unrelated containers or edit
discovered flow IDs by hand.

| Symptom | Likely cause | Safe action |
| --- | --- | --- |
| `init` cannot find a domain | Docker is unavailable, the volume name is unusual, or its mount is unreadable | Start Docker. Run `docker volume ls`, then rerun `init --domain-volume NAME` with the same account that will operate the demo. |
| `init` or `doctor` says the MXL domain is not readable | Docker volumes live below a root-only directory; Docker access alone does not grant file access | Run `init` and every later command with one account that can read the volume, for example `sudo python3 deploy/online-demo/mxl_demo.py init`. Do not loosen permissions on Docker's storage directory. |
| Licensed base image is missing | The MXL SDK runtime image has not been loaded on this host | Load the licensed image and rerun `init`, or supply its local tag with `--base-image`. The repository cannot redistribute it. |
| Doctor reports fewer than 4/8 paired sources | Wrong meeting, capture not authorized, outputs not mapped, audio/video pairing differs, or flows are stale | Confirm the intended meeting is active, authorize capture, map every numbered row to MXL video with matching audio, and wait for incoming-video activity before retrying. |
| ZoomISO **Join** spins indefinitely | The meeting is not active, the wrong meeting was selected, or the ZoomISO session is stale | Confirm the meeting link and host state. Restart only the ZoomISO instance if its UI remains stuck, then authorize and remap. Do not restart Core or unrelated infrastructure first. |
| ZoomISO warns about an NDI Discovery Server | An output was changed to NDI | For this workflow select **MXL**. NDI discovery is not used by the native MXL path. |
| Multiview tiles are frozen or black | ZoomISO flows rotated/reset, a source stopped advancing, or confidence telemetry is unhealthy | Run `check`. If the meeting reset, run `down`, reconnect/remap ZoomISO, confirm incoming video, then run `up`. |
| YouTube shows black/no data | The output was started before all video/audio gates were ready, or the source flows stopped | Stop the stream in the controller, run `check`, and restart only after every source and `audio_ready` are green. Never paste the stream key into a shell command. |
| YouTube bitrate is low or motion stutters | CPU saturation, a stalled source, or an unhealthy encoder—not automatically an insufficient configured bitrate | Run `check` and inspect the controller CPU/source metrics. Use the proven 32-logical-CPU profile or reduce source count; do not raise bitrate until cadence is healthy. |
| Only one participant is audible | ZoomISO audio rows are not all paired/live or the audience mix gate is not ready | Confirm every numbered output has matching audio and the controller reports all expected audio sources plus `audio_ready=true`. |
| `up` reports a started container did not stay running | A confidence encoder, Skin, or controller exited right after start, often because a flow vanished | `up` already removed the partial deployment. Read the private `launch-failure-*.log` it names under `state_dir`, fix the cause (usually remap ZoomISO), then rerun `up --apply`. |
| A configured port is occupied | Another deployment or service already owns it | Choose unused ports in `demo-config.json`. Do not stop the existing service merely to satisfy this demo. |
| Meeting resets during a run | The external test meeting or ZoomISO flow epoch changed | Run `down`, rejoin and remap, wait for advancing inputs, then run `up` and `check` again. |
| `down` refuses a same-named container | The container is not labelled as owned by this deployment | Leave it untouched. Change `deployment_id` or have its actual owner remove it. |

## Collecting a useful result

Run:

```sh
python3 deploy/online-demo/mxl_demo.py status
python3 deploy/online-demo/mxl_demo.py check --seconds 30
```

The detailed acceptance JSON is stored under the configured private
`state_dir`. Do not publish it verbatim: it can include runtime identifiers and
local paths. Public reports should contain only aggregate counters, resource
ranges, and the pass/fail outcome.

## Metered cloud hosts

`down` removes only the demo's seven owned containers. It does not stop the VM.
Use the cloud provider to stop/deallocate the machine and verify its state says
**deallocated**; a guest operating-system shutdown may still incur charges.

# Agent operator brief

The supported end-to-end demonstration is documented in `QUICKSTART.md` and
implemented by `deploy/online-demo/mxl_demo.py`. Read both completely before
changing or operating the bundle.

## Intended outcome

Produce a guarded ZoomISO Cloud → MXL Core demonstration with:

- four or eight paired MXL video/audio sources;
- stable Program and Preview flows;
- the ATEM-style browser Skin;
- one encoded Program confidence feed, one Preview feed, and one multiview
  wall; and
- a CPU-first 3840×2160 YouTube RTMPS output whose stream key remains only in
  the controller process.

## Operating rules

- Never place meeting credentials, participant names, stream keys, private
  addresses, flow UUIDs, or evidence paths in Git, logs, process arguments, or
  chat output.
- Do not automate Zoom authorization, participant mapping, or entry of the
  YouTube stream key. Ask the human to perform those steps.
- Run `./mxl-demo init`, `./mxl-demo preflight`, `./mxl-demo up --apply`,
  `./mxl-demo check`, and `./mxl-demo down --apply`; do not
  improvise a second deployment path. The longer bootstrap/launch commands are
  engineering diagnostics, not the first-time operator workflow.
- Keep the Skin and YouTube controller on loopback and use the SSH tunnel from
  `QUICKSTART.md`.
- Do not stop or replace ZoomISO, EasyMXL, meetings, Docker, networking, or
  unrelated containers. `stop --apply` may remove only the seven containers
  owned by the configured `deployment_id`. Ownership is the launcher's
  container label, not the name; `stop` refuses a same-named container without it.
- Demo containers have no restart policy. After a reboot or VM power-up they stay
  down until an operator relaunches against freshly discovered flows.
- The Skin's ME controls may use only the versioned `processing/layout-compositor`
  package. Build a scene on the off-air bank, verify Preview, then Take; never
  rebuild the on-air bank.
- If source flows rotate, stop the demo bundle, remap/confirm ZoomISO, and
  relaunch. Never hard-code discovered flow UUIDs.
- Treat a metered cloud VM as billable until its provider reports deallocated.

## Acceptance before calling a run successful

1. `./mxl-demo status` reports all nine owned containers running with zero
   restarts and all three control endpoints reachable.
2. Program, Preview, and the multiview wall visibly advance.
3. The YouTube controller's sustained gate in `outputs/rtmp/README.md` passes.
4. The audience player shows advancing picture and audible Program mix.
5. CPU and memory remain bounded with operating headroom.
6. Screenshots and public notes contain no identities, credentials, private
   addresses, UUIDs, or operational logs.

The eight-source audio mix has not yet passed its separate long audience A/V
acceptance gate. Report that limitation rather than promoting this prototype
as an unattended production appliance.

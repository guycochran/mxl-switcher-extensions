# Public-instructions-only outsider rehearsal

This is the release-candidate gate for the MXL Switcher technical preview. Its
purpose is to find hidden operator knowledge. It is not another feature or
performance test.

## Roles

- The **operator** has not used the private project handoff and follows only the
  public repository.
- The **observer** records questions and outcomes but does not supply commands,
  flow identities, private paths, or undocumented fixes.
- The **human license boundary** authorizes ZoomISO, maps participants, and
  enters a YouTube key when a destination test is included.

The operator may use an AI assistant, but the assistant receives only the
public repository and must follow `AGENTS.md`.

## Host starting state

- non-burstable x86-64 Linux host with measured CPU/RAM headroom;
- Docker, Git, and Python 3 installed;
- licensed ZoomISO Cloud and an authorized local MXL runtime image staged as
  documented in `PREREQUISITES.md`;
- no prior clone, `demo-config.json`, private state directory, or copied flow
  identity from another run; and
- controller ports reachable only over the documented SSH tunnel.

Existing unrelated services may remain running only when the operator selects
non-conflicting loopback ports through the documented configuration. Record
that fact; never stop an unrelated service to make the rehearsal pass.

## Operator script

The observer gives the operator only these starting links:

1. [Prerequisites](PREREQUISITES.md)
2. [Quickstart](../../QUICKSTART.md)
3. [Troubleshooting](TROUBLESHOOTING.md)

The operator then performs the documented lifecycle without an alternate
launch path:

```sh
git clone https://github.com/guycochran/mxl-switcher-extensions.git
cd mxl-switcher-extensions
./mxl-demo init
./mxl-demo preflight --config demo-config.json
./mxl-demo up --config demo-config.json --apply
./mxl-demo check --config demo-config.json --seconds 30
./mxl-demo down --config demo-config.json --apply
```

If automatic initialization is ambiguous, the operator may use only flags
already documented by `./mxl-demo init --help` and the quickstart.

## Scorecard

Record each item as pass, fail, or question:

| Gate | Required result |
| --- | --- |
| Prerequisites | Operator can identify every licensed and open-source dependency without private assistance |
| Initialization | One correct MXL domain and local base image selected without guessing |
| Preflight | All required checks pass and every warning has a clear next action |
| Launch | Seven owned services start and stay at zero restarts |
| Confidence | Program, Preview, and multiview visibly advance |
| Control | Preview and Take work; Program can be restored deliberately |
| Audio | Program mix is audible; any unqualified eight-source A/V limitation is reported |
| Acceptance | The 30-second check passes with zero hard-counter movement |
| Secrets | No meeting credential or stream key enters Git, logs, or process arguments |
| Cleanup | Only the seven labelled containers are removed; ZoomISO and unrelated services remain |
| Cloud stop | A metered VM reaches the provider's non-billing stopped/deallocated state |

Also record every question verbatim and the document section where the operator
expected to find the answer. Do not put private addresses, identities, flow
UUIDs, keys, meeting credentials, or raw operational logs in the public result.

## Decision

- **GO**: every gate passes with no undocumented intervention.
- **GO WITH DOC FIXES**: media and lifecycle gates pass, but one or more
  questions require public wording or navigation changes.
- **NO-GO**: any manual source-code/config surgery, private knowledge, failed
  acceptance check, leaked secret, or unsafe cleanup is required.

Only documentation, packaging, and safety defects discovered here belong in
this release-candidate cycle. Queue new features separately.

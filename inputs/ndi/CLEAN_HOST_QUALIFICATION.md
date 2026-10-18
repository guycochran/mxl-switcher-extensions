# NDI Input Bank clean-host qualification

`qualify_clean_host.py` is a guarded test harness for the experimental native
two-source split-payload copy path. It exists to answer one narrow question:
does `--copy-workers 1` meet strict cadence acceptance on a host without other
container workloads?

This is not a deployment tool. It does not qualify audio, recovery, more than
two sources, or production operation.

## Private prerequisites

Prepare these outside the repository and do not paste their contents into an
issue or public result:

- a two-source NDI bank JSON file in the format shown in this directory's
  README; both sources must use `audio_mode: "disabled"`;
- a Core facility config for those two inputs and qualification-only Program
  and Preview outputs;
- the matching Core output-definition directory;
- a locally available licensed NDI/MXL bank image; and
- a locally available immutable Core image.

The Core image must accept its normal `--config
/etc/mxl-switcher-core.conf` invocation. The bank image must contain this
repository at `/opt/mxl-switcher-extensions` and the licensed native runtime.
The harness resolves both supplied image references to content-addressed image
IDs before launch and runs those IDs, so a tag cannot move between preflight
and launch.

Because both containers use host networking, the Core config must use
`domain=/mxl-domain`, `control_host=127.0.0.1`, and a `control_port` equal to
the port in `--core-url` (which must also use `127.0.0.1`). It must contain
exactly two `source=` lines, and those
flow IDs must equal the two private bank video-flow IDs. The harness uses those
identities internally to find the right Core health records even when the API
returns additional or reordered sources; identities never enter the report.

The host must have no running Docker containers. This is deliberate: allowing
background services would turn a clean-host result into another shared-host
measurement. The harness refuses a busy host and never stops existing
containers.

The domain root must be a real directory, not a symlink, with at least 2 GiB
free. The evidence directory must be outside that root and have no ancestor or
descendant relationship with the run's MXL domain. Both health endpoints use
distinct loopback ports. NDI receive uses host networking; configure discovery
or direct addresses through the private bank file and licensed runtime, not in
the command line.

## Dry preflight

Choose a new lowercase deployment ID and new private evidence directory:

```sh
python3 inputs/ndi/qualify_clean_host.py \
  --deployment-id ndi-clean-001 \
  --bank-image YOUR_LOCAL_BANK_IMAGE \
  --core-image YOUR_IMMUTABLE_CORE_IMAGE \
  --source-config /private/path/bank.json \
  --core-config /private/path/core.conf \
  --definitions /private/path/definitions \
  --domain-root /dev/shm \
  --evidence-dir /private/path/ndi-clean-001
```

Preflight is read-only. It validates the two-source/video-only contract, image
availability, free ports, clean Docker host, private files, new domain, and new
evidence location. Its JSON contains only aggregate settings—never NDI
selectors, addresses, flow IDs, config contents, or host paths.

## Apply the staged gate

Repeat the same command with `--apply`. The harness then:

1. creates one private MXL domain with one second of history;
2. launches exactly one native NDI bank and one isolated Core, each with
   `--restart no`, ownership labels, and read-only private config mounts;
3. waits for bank and Core readiness;
4. observes a fixed 60-second smoke;
5. runs the fixed 180-second gate only when every smoke check passes;
6. writes aggregate `smoke.json`, optional `gate.json`, and `result.json` in
   the private evidence directory; and
7. removes only its two recorded container IDs and its marked MXL domain.

Cleanup inspects and removes containers by their recorded IDs, never their
names. It verifies the exact container ID, resolved image ID, deployment label,
and role label before removing either one. If any ownership fact changes, it
fails closed and retains the domain for an operator to inspect. It never uses a
name prefix, wildcard, Docker prune, image removal, or unrelated-service
mutation.

## Passing criteria

Both stages independently require:

- bank and Core stay ready with zero container restarts;
- both source heads advance between 28.5 and 30.5 fps with at most one frame
  of skew;
- Core routes frames and `held / (routed + held)` stays at or below 5 percent;
- zero delta for invalid, early/late, timeout, reader-reopen,
  synchronization, empty-window, regression, sustained-alarm, skip-event, and
  skipped-grain counters;
- no active sustained-unavailable alarm at the end;
- timing reports are present with zero catch-up ticks and zero frames behind;
- at least `floor(stage duration / 12)` ten-second timing reports are present;
- bank CPU stays at or below 300 percent (three CPU cores total); and
- bank RSS stays at or below 500 MiB (250 MiB per input);
- Core CPU stays at or below 200 percent and Core RSS at or below 512 MiB;
- neither process's ending RSS median grows more than the greater of 64 MiB or
  10 percent over its opening median; and
- every sample interval retains at least 20 percent whole-host idle CPU.

Host idle is read from `/proc/stat`; iowait counts as idle while steal time
counts as busy. Every sample also verifies that the only running Docker
containers are the two recorded candidate IDs. A new background container
therefore fails the qualification rather than silently contaminating it.

The gate measures source-head cadence, not semantic picture motion. Use sources
with obvious motion or a moving time reference and confirm them separately
before treating the result as content-liveness evidence.

Any missing/malformed health value or timing report fails the stage. A failed
60-second smoke prevents the longer gate. Results include the two resolved
image IDs but remain aggregate and suitable for public reporting. The evidence
directory is private because local operational context can still be sensitive.

## Offline verification

The Docker and HTTP boundaries are injectable, so ownership, redaction,
threshold, and staged-gate behavior can be tested without licensed software or
live infrastructure:

```sh
python3 -m unittest tests.test_ndi_clean_host -v
python3 -m unittest tests.test_ndi_input tests.test_ndi_bank -v
python3 scripts/validate.py
```

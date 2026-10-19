# Broadcast-readiness plan

This document is the promotion plan for the first supportable online-production
profile. It deliberately separates a compelling demonstration from a system an
engineer can trust on air.

The current [technical preview](TECHNICAL_PREVIEW.md) is intentionally narrower
than this promotion target. Its outsider rehearsal is the packaging gate for
what has already been demonstrated; it does not waive the destination,
recovery, or duration gates below.

## Product boundary

MXL Switcher Core owns deterministic Program and Preview identities, switching,
and authoritative routing state. Extensions own composition, codecs, transports,
recording, and hardware I/O. EasyMXL owns discovery, configuration, lifecycle,
and evidence collection. A failure in one extension must not interrupt Core or
another destination.

The first supported profile is intentionally narrow:

- four or six live MXL video sources;
- stable Program and Preview video plus 48 kHz Program audio;
- browser and Companion control backed by the same state;
- layouts and a diagnostic clock;
- one RTMPS destination; and
- a CPU-only local profile plus a fast-core cloud profile.

Meeting return, SDI, SRT, replay, and TAMS are not release blockers for this
profile. They remain independent promotion tracks.

## Promotion ladder

### 1. Deterministic offline gate

- All unit, schema, syntax, secret-redaction, and profile checks pass.
- Every queue has a bound and an explicit overflow policy.
- Output defaults preserve Program's 48 kHz audio rate.
- Health distinguishes source cadence, source lateness, composed cadence,
  encoded cadence, audio advancement, send-queue backlog, mux advancement,
  destination bytes, and reconnects.

### 2. Local receiver gate

- Independently decode both output essences.
- Verify requested video cadence and 48 kHz stereo audio.
- Verify content-level A/V offset remains within one video frame and drifts by
  less than 5 ms over 30 minutes.
- Require zero audio gaps, duplicates, regressions, and audible corrections.

### 3. Real destination gate

- Run `scripts/qualify_online_program.py` for at least 30 minutes so the Core,
  confidence feeds, destination path, and whole-host headroom share one evidence
  window and one verdict.
- Require every selected source, composition, encoder, audio path, mux, and
  destination byte counter to advance for every sample.
- Require zero reconnects and bounded RSS.
- Preserve at least 20 percent whole-host CPU headroom; the output process
  reports its own CPU-core and RSS range separately.
- Capture destination-side health plus two audience images at least ten seconds
  apart with visibly different diagnostic timecodes.

### 4. Recovery gate

While Program is live, exercise one fault at a time:

- source writer disappears and returns with a replacement identity;
- destination disconnects and reconnects;
- output process restarts;
- operator service restarts; and
- the normal upstream meeting reset occurs.

Each event must be visible to the operator, must not create duplicate writers,
and must recover without an ever-growing queue or A/V latency. Recovery-time
objectives must be declared by each adapter before it can become `candidate`.

### 5. Duration gate

- 10-minute qualification after deployment;
- 30-minute operated confirmation with frequent cuts and layout changes; and
- eight-hour unattended soak including an upstream reset.

The final evidence includes exact duration, cadence/rate ranges, A/V offset and
drift, reconnects, CPU/RSS ranges, counter deltas, cleanup, and exit status.

## Operator contract

The EIC surface uses four states:

- **green** — all required signals are advancing and the destination is fresh;
- **amber** — output remains usable but headroom or redundancy is degraded;
- **red** — on-air output is absent, stale, disconnected, or out of tolerance;
- **gray** — intentionally disabled or outside the selected profile.

An action acknowledgement is not a health result. For example, `start requested`
must remain amber until encoded media and destination bytes both advance. A
reconnect remains visible even after media resumes so a transient failure cannot
silently turn green.

## Resource profiles

| Profile | Intended use | Acceptance boundary |
|---|---|---|
| Local CPU | Daily development and four/six-source demonstrations | Reduced source count with at least 20% measured headroom |
| Fast cloud CPU | Eight-source and 4K acceptance | Full cadence, destination, recovery, and duration gates |
| Optional GPU | Dense layouts or multiple encoded destinations | Same gates; GPU is an optimization, not a Core dependency |

Claims about MXL efficiency must name the tested media format, topology, host,
and comparator. A controlled co-located CPU test can support that exact result;
it must not be generalized to every NDI or MXL deployment.

## Release decision

The online-production profile becomes `candidate` only when the local receiver,
real destination, and recovery gates pass on a clean deployment. It becomes
`accepted` only after the eight-hour gate and an external operator can deploy,
run, diagnose, and stop it using the published runbook without editing flow
identifiers or source code.

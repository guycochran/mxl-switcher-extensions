# MXL production design-partner pilot

## The offer

Bring one real media-production workflow and one decision that must be made.
The pilot turns that question into:

1. a working, bounded vertical slice;
2. measured cadence, drops, A/V timing, recovery, CPU, and memory evidence;
3. a repeatable deployment and operator handoff; and
4. a written **go**, **adapt**, or **stop** recommendation.

This is an engineering proof, not a promise to replace every function in OBS,
vMix, a production switcher, or a broadcast facility.

## Reference architecture

```text
contribution edge                       delivery edge
ZoomISO / NDI / funded adapter          RTMPS / funded adapter
             \                           /
              v                         v
          timestamped MXL flows -> deterministic Core
                         |          Program / Preview
                         +-> health, control, multiview
```

MXL is used where multiple functions on one host can share timestamped,
uncompressed media. NDI, SRT, SDI, ST 2110, IPMX, and other protocols remain
valid at the edges when the workflow needs them.

## A good first pilot

- one primary workflow and named operator outcome;
- a single modern CPU host by default;
- one contribution path and one delivery path;
- four to eight sources only when the selected host retains measured headroom;
- operator control plus fail-closed health;
- a fresh-host deployment rehearsal; and
- public-safe aggregate results plus a private engineering record.

Examples include a ZoomISO-to-stream production proof, a two-source NDI input
bank, or one vendor function integrated as an MXL extension.

## Success criteria are agreed first

Every pilot names its pass/fail thresholds before infrastructure is started.
Depending on the workflow, those thresholds include:

- exact video and audio rates;
- bounded A/V offset and drift;
- zero regressions, unexpected gaps, or hard routing errors;
- declared recovery behavior;
- bounded CPU and RSS with at least 20 percent host headroom;
- safe cleanup and rollback; and
- an outside operator completing the published lifecycle without source-code
  edits or private flow identities.

The customer sees failed evidence as well as passed evidence. A **stop** result
is useful when it prevents investment in the wrong architecture.

## What is not included by default

- a general-purpose broadcast platform or production SLA;
- universal protocol, codec, camera, or cloud compatibility;
- TAMS editing, trimming, and replay;
- a multi-host MXL fabric;
- unattended 24/7 operation; or
- features added only for demonstration novelty.

Commercial licenses, cloud resources, specialist hardware, and third-party
services are supplied by the customer or scoped separately.

## Existing proof

The [technical-preview evidence](TECHNICAL_PREVIEW.md) includes a CPU-only
ZoomISO/MXL-to-4K-YouTube demonstration and a strict two-source NDI video input
gate. Those results establish the starting platform; they do not automatically
qualify a new customer's sources, destination, host, or operating model.

## First conversation

Come prepared to answer:

> What production or vendor-integration decision must this pilot de-risk, who
> owns that decision, and what evidence would let the organization act?

The next step is a short workflow review and a fixed-scope proposal with named
success criteria, exclusions, operator responsibilities, and stop conditions.

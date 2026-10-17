# Deployment architecture decision

This project supports a **scale-up production core** as its default deployment.
MXL 1.1 makes a distributed production system credible, but it does not make a
multi-host topology the safest first deployment.

## Supported default: one production host

Run contribution adapters, MXL Switcher Core, Program Audio, graphics,
multiview, the operator Skin, and the primary encoder on one adequately sized
Linux host or VM. Components remain independently supervised processes, but
they exchange uncompressed media through the host's RAM-backed MXL domain.

This arrangement provides the clearest operational boundary:

- MXL connects cooperating media functions inside the production host;
- NDI, SRT, ST 2110, IPMX, or another appropriate transport crosses a machine
  or facility boundary; and
- RTMPS or SRT carries the finished Program to a delivery destination.

The deployment principle is:

> Scale up for the live production core. Scale out at controlled contribution,
> delivery, recording, and storage boundaries.

Keeping the live production core together avoids turning every internal media
connection into a network stream. It also reduces the number of flow-lifecycle,
timing, discovery, network, and recovery states an operator must diagnose
during a show.

## MXL 1.1 distributed preview

[MXL 1.1](https://github.com/dmf-mxl/mxl/releases/tag/v1.1.0) adds
`mxl-fabrics` for inter-host flow replication over libfabric, including
accelerated providers such as RoCEv2/Verbs and AWS EFA. It also adds a
GStreamer MXL plugin, multi-flow synchronization, ancillary-data tools, and
fixes relevant to writer jumps, reader ordering, missing flows, thread safety,
and reader/writer tail conflicts.

Those improvements justify a future distributed profile such as:

```text
contribution host       production host             delivery host
ZoomISO / NDI / SRT --> Core / audio / graphics --> encode / record / TAMS
                    MXL Fabrics                  MXL Fabrics or Program transport
```

This is a qualification target, not the currently supported operator path.
`mxl-fabrics` supplies media movement; the deployment must still own discovery,
capability exchange, flow identity, duplicate-writer prevention, liveness,
reconnection, and target reconstruction. The upstream documentation also
requires prompt completion-queue draining and appropriate queue sizing; an
overflow can stop transfer and require the target to be established again.

Ordinary virtual networking does not automatically make a three-VM deployment
better than one larger VM. A multi-host profile is most compelling when the
environment provides qualified accelerated networking or when operational
isolation has more value than the additional network and lifecycle complexity.

## Safe path to a distributed profile

Do not begin by moving every source across three hosts. Qualify the smallest
useful boundary first:

1. Upgrade and regress the accepted single-host stack against MXL 1.1.
2. Replicate one paired Program video/audio flow between two hosts.
3. Verify clean startup, orderly shutdown, writer replacement, receiver
   replacement, link interruption, and automatic recovery.
4. Require advancing-frame health rather than process-only health.
5. Prove bounded latency and resources with 10-minute, 30-minute, and eight-hour
   gates.
6. Expand to synchronized source flows only after the Program-pair gate passes.
7. Promote a three-host contribution/production/delivery profile only after
   recovery and duplicate-writer tests are repeatable.

The first three-host candidate should move contribution flows into the
production host and only one Program A/V pair into the delivery host. Recording,
TAMS, and streaming must remain outside the switcher's critical path and must
be able to fail without stopping Core.

## Current project decision

The single-host deployment is the product demonstrated by this repository.
The distributed MXL 1.1 topology is an advanced preview and roadmap item.

Development is intentionally paused at this boundary rather than expanding the
live system without a specific operator, customer, or funded demonstration.
Resume distributed work when all of the following are true:

- there is a concrete need that a larger single host cannot reasonably satisfy;
- two suitable hosts and their network capabilities are known;
- the test has an owner, a time box, and explicit pass/fail evidence;
- failure and recovery can be exercised without risking a production service;
  and
- a successful result will change a deployment or product decision.

This pause does not invalidate the proven system. It preserves a working,
explainable single-host vertical slice while keeping MXL 1.1 Fabrics as the
standards-aligned route to future scale-out.

# Cross-cloud host sizing

The accepted Azure reference for the eight-source, CPU-only demonstration was
`Standard_D32s_v5`: 32 vCPUs and 128 GiB RAM. No GPU was required. Use the
following machines for the first comparable Google Cloud and AWS tests.

| Provider | First test | vCPU | RAM | Status |
| --- | --- | ---: | ---: | --- |
| Azure | `Standard_D32s_v5` | 32 | 128 GiB | Accepted reference |
| Google Cloud | `n2-standard-32` | 32 | 128 GB | Exact-size test candidate |
| AWS | `m8i.8xlarge` | 32 | 128 GiB | Preferred current-generation test candidate |
| AWS | `m7i.8xlarge` | 32 | 128 GiB | Fallback when M8i is unavailable |

Google documents the [N2 machine
family](https://cloud.google.com/compute/docs/general-purpose-machines#n2_machine_types)
and AWS publishes the [EC2 general-purpose instance
specifications](https://docs.aws.amazon.com/ec2/latest/instancetypes/gp.html).
The Azure reference is listed in Microsoft's [Dsv5 size
table](https://learn.microsoft.com/azure/virtual-machines/sizes/general-purpose/dsv5-series).

For an optional second Google performance comparison, `c4-standard-32` has 32
vCPUs and 120 GB RAM on a newer compute platform. It is not the exact
memory-size match, so qualify `n2-standard-32` first and report the C4 result
separately.

## Selection rules

- Use on-demand, non-burstable, x86-64 capacity for the first qualification.
- Do not use AWS Flex, burstable, Spot, or Google Spot/preemptible capacity for
  an acceptance run. In particular, AWS documents M7i-Flex as providing only a
  percentage of full baseline CPU performance, which is unsuitable for a
  sustained software encoder gate.
- Do not substitute ARM/Graviton, C4A, or T2A yet. The current images and
  licensed dependencies have only been qualified on x86-64.
- Keep ZoomISO, the MXL shared-memory domain, Core, confidence services, and
  the YouTube adapter on the same VM. MXL shared memory does not span hosts.
- Do not add a GPU to the baseline. Test GPU acceleration later as a separate
  profile rather than changing the proven CPU-only architecture.
- Confirm regional availability and quota before scheduling the test. Prices
  vary by region and date, so use the provider calculator at test time rather
  than treating a price in this repository as authoritative.

## One comparable acceptance run

Use the same procedure and evidence on each provider:

1. Start one candidate VM and stage the licensed ZoomISO application plus the
   compatible operator-controlled MXL runtime image.
2. Map eight native MXL outputs with matching audio and confirm that all rows
   advance.
3. Run `./mxl-demo init`, `preflight`, `up --apply`, and the 30-second `check`.
4. Send the 3840x2160p30 Program to an unlisted YouTube event. Confirm eight
   moving tiles, the moving diagnostic clock, and all-source Program audio.
5. Record VM CPU, per-service CPU/RSS, output bitrate, frame/drop counters,
   restarts, Core hard counters, and the audience observation interval.
6. Run `./mxl-demo down --apply`, then stop the VM at the provider level and
   verify the final state.

The provider stop-state wording differs:

- Azure: **Stopped (deallocated)**
- Google Cloud: **TERMINATED** (stopped, not deleted)
- AWS: **stopped**

Stopped compute no longer accrues normal VM runtime charges, but disks,
snapshots, reserved/static public addresses, and other attached resources can
still cost money. Verify them separately. Google documents its [stop
behavior](https://cloud.google.com/compute/docs/instances/stop-start-instance),
and AWS documents the [instance
lifecycle](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-lifecycle.html).

Until these runs pass, describe Google Cloud and AWS as portable deployment
candidates—not qualified platforms.

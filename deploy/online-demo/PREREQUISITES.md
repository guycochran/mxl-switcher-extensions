# Prerequisite onboarding

This page covers the part of the online demonstration that the public
repository cannot install: the licensed ZoomISO Cloud application and a
compatible operator-controlled MXL runtime image. Once the checkpoint at the bottom of this page passes, use
the [end-to-end quickstart](../../QUICKSTART.md) without adding a second launch
path.

## What comes from where

| Item | Supplied by | Stored where | Checked by the launcher |
| --- | --- | --- | --- |
| Linux host, Docker, Git, and Python 3 | Operator or cloud provider | Host | Yes |
| ZoomISO Cloud container/image and license | Operator's authorized ZoomISO source | Local Docker image and running container | Indirectly, through its live MXL domain |
| Open-source MXL SDK and `gst-mxl-rs`, plus codec plugins | Operator builds or obtains | Operator-controlled local base image | Yes, item by item |
| Core, Skin, confidence monitors, and YouTube adapter | This public repository plus pinned public dependencies | Built locally by `up` | Yes |
| Meeting authorization, participant mapping, YouTube destination, and stream key | Human operator | Interactive applications or process memory only | Deliberately not automated |

The repository contains no vendor credentials, private registry address,
prebuilt runtime image, meeting credential, or stream key. MXL itself is
Apache-2.0 open source, but that does not automatically grant permission to
redistribute a complete image containing ZoomISO or separately licensed codec
components. A successful lab run does not grant permission to copy its private
images to another person or cloud account.

## 1. Prepare one host

Use one non-burstable x86-64 Linux VM for ZoomISO, the MXL shared-memory
domain, Core, confidence monitoring, and the YouTube adapter. MXL shared memory
does not span hosts. The demonstrated eight-source CPU profile used 32 modern
logical CPUs and 128 GiB RAM. A four-source rehearsal can start smaller, but a
host at sustained CPU saturation is not a valid acceptance result.

Install Docker Engine, Git, and Python 3 using the operating system or cloud
provider's supported method. Give the intended operator account stable Docker
access and read access to the selected MXL domain. Do not make Docker's storage
tree world-readable to work around permissions.

Keep the browser controllers bound to loopback and reach them through the SSH
tunnel in the quickstart. The host needs outbound connectivity required by the
licensed ZoomISO application, Git dependency fetches, the pinned container
image fetch, and YouTube RTMPS. Follow the vendors' current network guidance;
do not open the operator web ports to the public Internet.

## 2. Stage the licensed software

Obtain ZoomISO Cloud through its authorized vendor channel and use the vendor's
documented installation procedure. Build the Apache-2.0 MXL SDK and
`gst-mxl-rs` plugin from the public Media eXchange Layer project, or obtain an
operator-controlled image containing them. Use an MXL revision compatible with
the installed ZoomISO release; confirm that revision with the ZoomISO vendor or
its release notes.

Review the licenses of every codec component before distributing a complete
image. If an image that the operator is permitted to use is delivered as a
Docker archive, load it locally without committing the archive or its name to
Git:

```sh
docker load --input /operator-controlled/path/licensed-image.tar
docker image ls
```

If the authorized source is a private registry, authenticate interactively with
the provider's credential helper. Plain `docker login` without a credential
helper can leave a base64-encoded token in `~/.docker/config.json`; keep that
file out of this repository and every evidence bundle. Do not put a registry
password or token in `demo-config.json`, shell history, or a launcher argument.

The public launcher accepts any local base-image tag. Retagging is optional:

```sh
docker tag VENDOR_OR_OPERATOR_TAG mxl-lab:latest
```

Whatever tag is selected must satisfy the complete, machine-checked
[base-image contract](BASE_IMAGE.md). A matching tag by itself is not proof.
The launcher runs a short probe inside the image and reports every missing
library, header, tool, Python module, or GStreamer element.

Start the licensed ZoomISO container using its supported deployment method so
its MXL domain is visible to this same host as either:

- a named Docker volume; or
- an absolute host path or tmpfs path.

Do not copy live MXL shared-memory files between machines. Do not substitute
NDI for the MXL outputs in this workflow.

## 3. Confirm the operator boundary

Before cloning or launching the public bundle, the operator should be able to
answer **yes** to all of these:

- Docker runs successfully for the same account that will operate the demo.
- The licensed ZoomISO UI opens and its license is active.
- The intended meeting can be joined and capture can be authorized.
- Four or eight numbered ZoomISO outputs can be set to native MXL with matching
  audio and show advancing incoming video.
- The MXL domain is a known named volume or readable absolute path.
- An authorized local base image is present and its tag is known.
- The base image's MXL SDK and `mxlsrc` versions are compatible with the MXL
  version used by the installed ZoomISO release.
- The host is x86-64, non-burstable, and has enough CPU/RAM headroom for the
  selected source count.
- The operator can create the documented SSH tunnel; no controller port needs
  to be exposed publicly.
- On a metered VM, someone is responsible for verifying **deallocated** after
  cleanup.

If any answer is no, stop at this boundary. The public launcher cannot repair a
license, authorize a meeting, invent an SDK image, or make shared memory span
hosts.

## 4. Hand control to the public launcher

Run `init` with the exact domain source and image tag when automatic detection
is not unambiguous:

```sh
./mxl-demo init \
  --domain-volume YOUR_ZOOMISO_MXL_VOLUME \
  --base-image YOUR_LOCAL_MXL_IMAGE
```

For a host bind mount or tmpfs, replace `--domain-volume` with
`--domain-path /absolute/path`. For a smaller rehearsal, add
`--source-count 4`. Select exactly one domain source.

Then use only the supported operator sequence:

```sh
./mxl-demo preflight --config demo-config.json
./mxl-demo up --config demo-config.json --apply
./mxl-demo check --config demo-config.json --seconds 30
./mxl-demo down --config demo-config.json --apply
```

`preflight` is the plain-language alias for the existing read-only `doctor`
command and is the go/no-go checkpoint. It must report the Docker API, live paired
source count, local base image, and runtime-image contract as `PASS`.
Warnings about images built by `up` are expected on the first run. Do not work
around a failed licensed-image probe by editing the launcher.

## Cloud portability boundary

The bundle has passed its published operator rehearsal on a CPU-only Azure VM
with the required prerequisites preinstalled, alongside existing services.
Its architecture is provider-neutral and should transfer to equivalent
non-burstable x86-64 instances on AWS or Google Cloud, provided the operator
stages a licensed ZoomISO instance and the same compatible runtime prerequisites
on that host. That is
portability guidance, not an AWS or Google Cloud acceptance claim. Repeat
`preflight`, the 30-second gate, the audience check, cleanup, and provider-level
deallocation on each new platform before describing it as qualified.

# Build the MXL base image from public source

This recipe produces an operator-controlled base image from public, open-source
components only. It satisfies the [base-image contract](../BASE_IMAGE.md) and is
an alternative to obtaining a prebuilt image.

## What it contains

- The **MXL SDK `v1.1.0`** (Apache-2.0), built from
  [`dmf-mxl/mxl`](https://github.com/dmf-mxl/mxl) with its
  `Linux-Clang-Release` preset and installed to `/usr/local`. The headers are
  also copied to `/src/mxl/lib/include`, the path Core's pinned runtime
  Dockerfile compiles against.
- The **`gst-mxl-rs`** GStreamer plugin (`mxlsrc`/`mxlsink`) from the same MXL
  release, built with Cargo.
- Ubuntu 24.04's **GStreamer 1.24**: the base, good, bad, ugly and libav
  plugin sets, which together contain every element the contract lists.
- **Python 3** with PyGObject, NumPy and OpenCV, **Cairo**, and a **C++20
  toolchain** with Make.

## Version pairing

The MXL release must match the MXL library bundled in your ZoomISO Cloud
release, because readers and ZoomISO's writer share ring-buffer memory.
**ZoomISO Cloud 0.1.0.77 bundles `libmxl.so.1.1`, so this recipe pins
`MXL_REF=v1.1.0`.** For another ZoomISO version, check its bundled
`libmxl.so.*` and set `MXL_REF` to the matching MXL release tag:

```sh
docker build --build-arg MXL_REF=v1.1.0 -t mxl-base-oss:1.1.0 deploy/online-demo/base-image
```

## Build and use

```sh
docker build -t mxl-base-oss:1.1.0 deploy/online-demo/base-image
./mxl-demo init --base-image mxl-base-oss:1.1.0
./mxl-demo preflight --config demo-config.json
```

`preflight` runs the contract probe inside the image. The `operator base-image
contract` line must show `PASS` before you continue.

- **Build time:** about 20 minutes on a 32-vCPU x86-64 VM.
- **Network:** internet access is needed to fetch Ubuntu packages, the vcpkg
  tool and its pinned dependencies, the Rust toolchain, and the MXL source.
- **Size:** the final image is about 2.3 GB.

## Verification record

This exact recipe was built on a fresh Google Cloud `n2-standard-32` with
Ubuntu 24.04, with ZoomISO Cloud 0.1.0.77 as the MXL writer. `preflight`
passed the full contract on the first attempt, and the public `init`,
`preflight`, `up --apply`, `check` and `down --apply` sequence ran to
completion. That is one verified pairing, not a guarantee for every ZoomISO,
MXL or Ubuntu combination.

## Licensing

MXL and `gst-mxl-rs` are Apache-2.0. The image also includes distribution
codec plugins, such as GPL `x264` in `gstreamer1.0-plugins-ugly` and FFmpeg in
`gstreamer1.0-libav`, whose licences govern redistribution. Build the image
locally; this repository does not publish it. ZoomISO Cloud remains a
separately licensed application and is not part of this image.

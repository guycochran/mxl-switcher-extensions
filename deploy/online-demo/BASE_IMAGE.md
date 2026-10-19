# Operator-supplied MXL base-image contract

The online demo needs one **operator-built or operator-supplied local container
image**. The MXL SDK and its `gst-mxl-rs` GStreamer plugin are open source under
Apache-2.0. The image also needs codec plugins whose licenses govern how the
complete image may be redistributed; for example, x264 is GPL, while FFmpeg's
enabled components and license depend on how it was built. This repository does
not publish the image. ZoomISO Cloud remains a separately licensed application.

`mxl-lab:latest` is only the lab's default local tag. It is not a public image
name and the launcher never attempts to pull it. A platform may use any tag:

```sh
./mxl-demo init --base-image YOUR_LOCAL_MXL_IMAGE
```

The image must contain the following runtime and build contract:

- Linux `libmxl.so` plus the MXL headers at `/src/mxl/lib/include/mxl` (these
  paths are what Core's pinned runtime Dockerfile compiles against; an image
  that installs MXL elsewhere must provide them, for example with symlinks);
- a C++20 compiler and Make, used to build the pinned Core revision;
- Python 3 with PyGObject/GStreamer, NumPy, and OpenCV;
- GStreamer 1.24 or later with `gst-mxl-rs` `mxlsrc`;
- the elements used by the confidence and YouTube paths: `appsrc`, `queue`, `identity`,
  `x264enc`, `h264parse`, `flvmux`, `rtmpsink`, `rtmp2sink`, `compositor`,
  `videoscale`, `videoconvert`, `videotestsrc`, `cairooverlay`, `audiomixer`,
  `audioconvert`, `audioresample`, `rglimiter`, `avenc_aac`, and `aacparse`;
- `libcairo.so.2` for the moving diagnostic clock.

`preflight` launches a short, credential-free probe inside the selected image and
fails with the missing contract items before any build or service launch. This
is the supported compatibility test; a matching image name alone is not proof.

To build a conforming image from public sources only, use the
[from-source base-image recipe](base-image/README.md).

For a clean-host rehearsal, load or build this operator-controlled image before running
`init`, and record that it was a preloaded prerequisite. Do not publish the
image or infer redistribution permission from a successful local run.

For the complete operator-side staging checklist, credential boundary, and
provider-neutral handoff into `init`, see
[`PREREQUISITES.md`](PREREQUISITES.md).

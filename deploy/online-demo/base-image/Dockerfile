# SPDX-License-Identifier: Apache-2.0
# Operator-built MXL base image for the online demo, from public open source only.
# It satisfies deploy/online-demo/BASE_IMAGE.md. MXL v1.1.0 matches the
# libmxl.so.1.1 bundled in ZoomISO Cloud 0.1.0.77; change MXL_REF only to the
# MXL release your ZoomISO version bundles. x86-64 only.
#
#   docker build -t mxl-base-oss:1.1.0 deploy/online-demo/base-image
#
# The image is built locally and is not published by this repository. Review
# the codec licenses (for example GPL x264 in gstreamer1.0-plugins-ugly)
# before redistributing a built image.
FROM ubuntu:24.04 AS build
ARG DEBIAN_FRONTEND=noninteractive
ARG MXL_REF=v1.1.0
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential clang lld cmake ninja-build pkg-config git curl zip unzip tar \
      ca-certificates python3 rustup libclang-dev bison flex autoconf automake libtool nasm \
      libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libglib2.0-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/microsoft/vcpkg /root/vcpkg \
    && /root/vcpkg/bootstrap-vcpkg.sh --disableMetrics
ENV VCPKG_ROOT=/root/vcpkg
RUN git clone --depth 1 --branch ${MXL_REF} https://github.com/dmf-mxl/mxl.git /src/mxl
WORKDIR /src/mxl
RUN cmake --preset Linux-Clang-Release -DBUILD_DOCS=OFF -DBUILD_TESTS=OFF \
      -DCMAKE_INSTALL_PREFIX=/usr/local -DCMAKE_INSTALL_LIBDIR=lib \
    && cmake --build build/Linux-Clang-Release -j"$(nproc)" \
    && cmake --install build/Linux-Clang-Release
RUN rustup default stable \
    && cd rust && cargo build --release -p gst-mxl-rs \
    && find target/release -maxdepth 1 -name 'libgst*.so' -exec cp {} /tmp/ \;

FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential make python3 python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
      python3-numpy python3-opencv libcairo2 \
      gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
      gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/lib/ /usr/local/lib/
COPY --from=build /usr/local/include/ /usr/local/include/
# Core's pinned runtime Dockerfile compiles against /src/mxl/lib/include.
COPY --from=build /src/mxl/lib/include/ /src/mxl/lib/include/
COPY --from=build /usr/local/include/mxl/ /src/mxl/lib/include/mxl/
COPY --from=build /tmp/libgst*.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/
RUN ldconfig
LABEL org.opencontainers.image.title="mxl-base-oss" \
      org.opencontainers.image.description="MXL v1.1.0 + gst-mxl-rs + GStreamer 1.24 runtime, built from public sources" \
      org.opencontainers.image.licenses="Apache-2.0 AND GPL-2.0-or-later AND LGPL-2.1-or-later"

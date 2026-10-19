ARG BASE_IMAGE=mxl-lab:latest
FROM ${BASE_IMAGE}

WORKDIR /opt/mxl-layout-compositor
COPY processing/layout-compositor/layout_compositor.py ./
COPY processing/layout-compositor/scene_supervisor.py ./

ENV PYTHONUNBUFFERED=1 \
    GST_REGISTRY=/tmp/gstreamer-layout-registry.bin

ENTRYPOINT ["python3", "/opt/mxl-layout-compositor/scene_supervisor.py"]

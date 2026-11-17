FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir kleinanzeigen-api==0.4.0
COPY archive.py /app/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ARG BUILD_REVISION=unknown
ENV BUILD_REVISION=${BUILD_REVISION}
LABEL org.opencontainers.image.revision=${BUILD_REVISION}
USER 99:100

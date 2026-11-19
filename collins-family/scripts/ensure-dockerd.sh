#!/bin/bash
# Start the box-local dockerd if it is not already running.
# Docker was installed from official static binaries (not apt) because the
# Debian apt mirror returned HTTP 500 for InRelease through the box proxy.
set -euo pipefail
export PATH="/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin:${PATH:-}"
if ! docker info >/dev/null 2>&1; then
  if [[ ! -x /usr/local/bin/dockerd ]]; then
    echo "dockerd is not installed at /usr/local/bin/dockerd" >&2
    exit 1
  fi
  sudo mkdir -p /var/lib/docker /var/run/docker
  sudo env PATH="$PATH" dockerd \
    --data-root /var/lib/docker \
    --host unix:///var/run/docker.sock \
    --pidfile /var/run/docker.pid \
    >> /tmp/dockerd.log 2>&1 &
  for i in $(seq 1 20); do
    if docker info >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  if ! docker info >/dev/null 2>&1; then
    echo "dockerd failed to start; see /tmp/dockerd.log" >&2
    exit 1
  fi
  # box user needs access to the socket
  sudo chmod 666 /var/run/docker.sock || true
fi
echo "Docker daemon is running."

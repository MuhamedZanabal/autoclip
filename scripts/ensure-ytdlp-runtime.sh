#!/bin/bash
# Re-apply yt-dlp + Deno/EJS + cookie patches inside AutoClip containers after recreate.
set -euo pipefail
for c in autoclip-app autoclip-celery-worker; do
  docker exec -u root "$c" bash -lc '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq unzip curl ca-certificates >/dev/null
    export DENO_INSTALL=/usr/local
    if ! command -v deno >/dev/null 2>&1; then
      curl -fsSL https://deno.land/install.sh | sh
    fi
    pip install -q -U "yt-dlp[default]" yt-dlp-ejs
  '
  docker cp /opt/autoclip/backend/utils/ffmpeg_utils.py "$c":/app/backend/utils/ffmpeg_utils.py
  docker cp /opt/autoclip/backend/services/studio/jobs.py "$c":/app/backend/services/studio/jobs.py
  docker cp /opt/autoclip/backend/utils/download_recovery.py "$c":/app/backend/utils/download_recovery.py
done
docker restart autoclip-app autoclip-celery-worker

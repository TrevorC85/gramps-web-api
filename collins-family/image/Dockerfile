# Collins Family Gramps Web image: our gramps-web-api fork + our frontend
# fork, built from source (not layered over the official image).
#
# Build with ./build.sh, which assembles the context from two committed
# trees (backend/ = gramps-web-api fork, frontend/ = gramps-web fork) and
# passes their commit SHAs. All base images are pinned by digest and all
# Python packages by version (constraints-26.11.2.txt = the official
# 26.11.2 image's pip freeze).
#
# Compared with the official image this leaves out what the Collins site
# does not use: the AI chat dependencies (pydantic-ai, provider SDKs), the
# PostgreSQL client and addons, CJK fonts and the OCR languages other than
# English. Face detection (OpenCV), PDF/video thumbnails, Graphviz reports
# and English OCR stay.

ARG DEBIAN=debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6
ARG DEBIAN_SLIM=debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
ARG NODE=node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54

# ---------------------------------------------------------------- frontend
FROM ${NODE} AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build && test -f dist/index.html

# ----------------------------------------------------------- python wheels
FROM ${DEBIAN} AS wheels
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config python3-dev python3-venv \
    libcairo2-dev libgirepository1.0-dev libicu-dev \
  && rm -rf /var/lib/apt/lists/*
COPY constraints-26.11.2.txt /constraints.txt
COPY backend/ /src/
RUN python3 -m venv /venv \
  && /venv/bin/pip install --no-cache-dir --upgrade pip wheel \
  && /venv/bin/pip install --no-cache-dir -c /constraints.txt \
       gunicorn 'opencv-python-headless>=4.8,<6' /src \
  && /venv/bin/pip uninstall -y pip wheel \
  && find /venv -name '__pycache__' -prune -exec rm -rf {} +

# -------------------------------------------------------------------- final
FROM ${DEBIAN_SLIM}
ARG BACKEND_SHA=unknown
ARG FRONTEND_SHA=unknown
ARG ADDONS_SHA=5c0f1a7b4bf9aa91d71cd19be21e07496924822c
ENV DEBIAN_FRONTEND=noninteractive GRAMPS_VERSION=60
# Gramps addons used by the API (FilterRules, JSON) are pinned to one commit
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 \
    fonts-dejavu-core fonts-noto-core \
    libcairo2 libcairo-gobject2 libgirepository-1.0-1 libicu76 \
    gir1.2-gtk-3.0 gir1.2-pango-1.0 gir1.2-gexiv2-0.10 gir1.2-osmgpsmap-1.0 \
    graphviz locales gettext \
    poppler-utils poppler-data ffmpeg \
    tesseract-ocr tesseract-ocr-osd tesseract-ocr-eng \
    wget unzip \
  && localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8 \
  && mkdir -p /root/gramps/gramps$GRAMPS_VERSION/plugins \
  && wget -q -O /tmp/addons.zip https://github.com/gramps-project/addons/archive/${ADDONS_SHA}.zip \
  && for addon in FilterRules JSON; do \
       unzip -p /tmp/addons.zip addons-${ADDONS_SHA}/gramps$GRAMPS_VERSION/download/$addon.addon.tgz \
       | tar -xz -C /root/gramps/gramps$GRAMPS_VERSION/plugins; \
     done \
  && rm /tmp/addons.zip \
  && apt-get purge -y wget unzip && apt-get autoremove -y \
  && rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/*
ENV LANGUAGE=en_US.utf8 LANG=en_US.utf8 LC_ALL=en_US.utf8 OMP_NUM_THREADS=1
COPY --from=wheels /venv /venv
ENV PATH="/venv/bin:$PATH"
# same paths and settings as the official image
ENV GRAMPS_API_CONFIG=/app/config/config.cfg \
    GRAMPSWEB_USER_DB_URI=sqlite:////app/users/users.sqlite \
    GRAMPSWEB_MEDIA_BASE_DIR=/app/media \
    GRAMPSWEB_SEARCH_INDEX_DB_URI=sqlite:////app/indexdir/search_index.db \
    GRAMPSWEB_STATIC_PATH=/app/static \
    GRAMPSWEB_THUMBNAIL_CACHE_CONFIG__CACHE_DIR=/app/thumbnail_cache \
    GRAMPSWEB_REQUEST_CACHE_CONFIG__CACHE_DIR=/app/cache/request_cache \
    GRAMPSWEB_PERSISTENT_CACHE_CONFIG__CACHE_DIR=/app/cache/persistent_cache \
    GRAMPSWEB_REPORT_DIR=/app/cache/reports \
    GRAMPSWEB_EXPORT_DIR=/app/cache/export \
    GRAMPSHOME=/root \
    GRAMPS_DATABASE_PATH=/root/.gramps/grampsdb
RUN mkdir -p /app/src /app/config /app/db /app/media /app/indexdir /app/users \
      /app/thumbnail_cache /app/cache/reports /app/cache/export \
      /app/cache/request_cache /app/cache/persistent_cache /app/tmp /app/persist \
  && touch /app/config/config.cfg \
  && python3 -c "import yclade; yclade.tree.download_yfull_tree()"
# the entrypoint runs the user DB migrations from /app/src
COPY backend/alembic.ini /app/src/alembic.ini
COPY backend/alembic_users /app/src/alembic_users
COPY backend/docker-entrypoint.sh /docker-entrypoint.sh
COPY --from=frontend /src/dist /app/static
WORKDIR /app
EXPOSE 5000
LABEL org.opencontainers.image.title="grampsweb-collins" \
      org.opencontainers.image.description="Gramps Web 26.11.2 + Collins tree link lines (relation colour, confidence width)" \
      org.opencontainers.image.version="26.11.2-collins" \
      org.opencontainers.image.revision="${BACKEND_SHA}" \
      collins.backend.sha="${BACKEND_SHA}" \
      collins.frontend.sha="${FRONTEND_SHA}" \
      collins.addons.sha="${ADDONS_SHA}"
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD exec gunicorn -w ${GUNICORN_NUM_WORKERS:-8} -b 0.0.0.0:5000 gramps_webapi.wsgi:app --timeout ${GUNICORN_TIMEOUT:-120} --limit-request-line 8190 --preload

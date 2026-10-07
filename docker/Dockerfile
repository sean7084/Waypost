# syntax=docker/dockerfile:1
#
# Waypost production image: Django 5.2 + Gunicorn on Debian slim.
#
# Build it through Compose from the repository root (see docker-compose.yml):
#
#     docker compose build app
#     docker build -f docker/Dockerfile -t waypost-app .
#
# This image carries three things a plain Django image would not, because three
# code paths shell out to native tools:
#
#   1. WeasyPrint renders quotation/delivery PDFs from HTML
#      (quotations/services.py, deliveries/services.py) -> Pango + GDK-Pixbuf.
#   2. `soffice --headless --convert-to pdf` turns filled xlsx templates into
#      PDFs (convert_xlsx_to_pdf in quotations/deliveries/invoices) ->
#      libreoffice-calc. The code returns None when soffice is missing, so a
#      broken install degrades silently: that is why the build asserts on it.
#   3. Generated documents carry Chinese text (COMPANY_NAME_ZH is
#      上海珩际信息科技有限公司) -> fonts-noto-cjk. Without a CJK font both
#      WeasyPrint and LibreOffice emit empty boxes and nothing errors.
#
# The container runs as root *inside* itself. Production is rootless Docker,
# where container root maps to the host user that owns the daemon (`deploy`), so
# files written to the bind-mounted media directory land as deploy:deploy with no
# uid guessing. Running as an arbitrary non-zero uid instead is what produces the
# "directory looks like nobody:nogroup" failure documented for the Helpdesk
# stack. See docs/DEPLOYMENT.md.

ARG BASE_IMAGE=python:3.12-slim-bookworm

# ---------------------------------------------------------------------------
# base: apt mirror configuration shared by both stages.
# Defaults point at Aliyun because the production ECS is in cn-heyuan; the
# server's .env overrides them with the internal mirror, which is free of public
# bandwidth charges.
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS base

ARG APT_MIRROR=mirrors.aliyun.com

RUN set -eux; \
    for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        [ -f "$f" ] || continue; \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" "$f"; \
    done

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# ---------------------------------------------------------------------------
# deps: install requirements.txt into a venv that the runtime stage copies.
# The toolchain lives only here, so compilers never reach the final image.
# ---------------------------------------------------------------------------
FROM base AS deps

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Copied on its own so a source-only change reuses this (slowest) layer.
# requirements.txt pins psycopg2-binary and every other native dependency to a
# version with cp312 wheels, so the normal path never touches the toolchain
# above; gunicorn's `sys_platform != 'win32'` marker installs it here.
COPY requirements.txt ./
RUN pip install --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" \
        -r requirements.txt

# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM base AS runtime

# WeasyPrint's native libraries are the same set backend-ci.yml installs, plus
# librsvg2-common (SVG inside documents) and fontconfig (font discovery).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libharfbuzz0b \
        libffi8 \
        librsvg2-common \
        shared-mime-info \
        fontconfig \
        fonts-noto-cjk \
        tzdata \
        ca-certificates \
        curl \
    && (apt-get install -y --no-install-recommends libreoffice-calc-nogui \
        || apt-get install -y --no-install-recommends libreoffice-calc) \
    && rm -rf /var/lib/apt/lists/*

# Assert the native capabilities the app assumes, rather than discovering their
# absence later as a user-facing "PDF converter not found" warning or as empty
# boxes where Chinese text should be.
RUN set -eux; \
    if ! command -v soffice >/dev/null 2>&1; then \
        for c in /usr/lib/libreoffice/program/soffice /usr/bin/libreoffice; do \
            if [ -x "$c" ]; then ln -s "$c" /usr/local/bin/soffice; break; fi; \
        done; \
    fi; \
    command -v soffice; \
    test -n "$(fc-list :lang=zh)"; \
    fc-list :lang=zh | head -3

# Warm the LibreOffice user profile at build time: the first conversion then
# does not pay for profile creation inside a request, and a broken install
# surfaces here instead.
RUN timeout 180 soffice --headless --terminate_after_init >/dev/null 2>&1 || true

COPY --from=deps /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    DJANGO_SETTINGS_MODULE=waypost.settings \
    TZ=Asia/Shanghai

# End-to-end proof that the PDF toolchain works, including CJK glyphs. Rendering
# a real document at build time is what turns "fonts-noto-cjk forgot to install"
# from a silent production defect into a failed build.
RUN set -eux; \
    python -c "import weasyprint; weasyprint.HTML(string='<p>上海珩际信息科技有限公司 Waypost</p>').write_pdf('/tmp/weasyprint-probe.pdf')"; \
    test -s /tmp/weasyprint-probe.pdf; \
    rm -f /tmp/weasyprint-probe.pdf

WORKDIR /app
COPY . .

# *.mo files are gitignored, so translations are compiled here. The script is
# polib-only (no gettext/msgfmt needed) and deliberately does not call
# django.setup(), so it requires no settings and no environment.
RUN python compile_translations.py

# CRLF insurance: this repository is developed on Windows, and a stray carriage
# return in a script makes the kernel look for an interpreter named
# `/bin/bash^M`. .gitattributes already pins eol=lf; this is the backstop.
RUN set -eux; \
    find docker -name '*.sh' -print0 | xargs -0 --no-run-if-empty sed -i 's/\r//'; \
    find docker -name '*.sh' -print0 | xargs -0 --no-run-if-empty chmod +x; \
    test -x docker/entrypoint.sh

# collectstatic is NOT run here: CompressedManifestStaticFilesStorage loads
# settings, and settings.py fails fast without DJANGO_SECRET_KEY. The entrypoint
# runs it once the real environment is present.
EXPOSE 8000

# 200 from /healthz/ means the app booted, the database answered a query and the
# cache round-tripped. urllib raises HTTPError on the 503 path, which is a
# non-zero exit - exactly what Docker needs to mark the container unhealthy.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=5)" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]

# Gunicorn reads GUNICORN_CMD_ARGS from the environment, so worker/thread counts
# can be tuned per host without rebuilding (e.g. GUNICORN_CMD_ARGS="--workers 2").
# --timeout is 300s because xlsx->PDF conversion allows 120s per document and the
# Nginx proxy_read_timeout is set to match.
CMD ["gunicorn", "waypost.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--capture-output"]

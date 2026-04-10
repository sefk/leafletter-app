FROM python:3.13-slim

# System dependencies:
#   gdal-bin + libgdal-dev  — GeoDjango / GDAL Python bindings
#   default-libmysqlclient-dev + pkg-config — mysqlclient compilation
#   build-essential — C compiler for pip packages that build from source
RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin \
        libgdal-dev \
        default-libmysqlclient-dev \
        default-mysql-client \
        pkg-config \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Put the uv-managed venv on PATH so subsequent RUN steps and start_web.sh
# use the project Python/packages without needing `uv run`.
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./

# Install Python deps, then reinstall GDAL pinned to the system library version
# so the Python bindings and libgdal.so are guaranteed to match.
RUN uv sync --frozen --no-dev \
    && uv pip install --no-cache-dir --force-reinstall "GDAL==$(gdal-config --version)"

COPY . .

RUN DEBUG=False python manage.py collectstatic --noinput && chmod +x start_web.sh

# Railway injects $PORT; fall back to 8000 for local docker run.
# start_web.sh runs migrate then binds to 0.0.0.0:$PORT.
CMD ["/bin/bash", "start_web.sh"]

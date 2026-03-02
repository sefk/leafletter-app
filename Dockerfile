FROM python:3.13-slim

# System dependencies:
#   gdal-bin + libgdal-dev  — GeoDjango / GDAL Python bindings
#   default-libmysqlclient-dev + pkg-config — mysqlclient compilation
#   build-essential — C compiler for pip packages that build from source
RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin \
        libgdal-dev \
        default-libmysqlclient-dev \
        pkg-config \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# Install Python deps, then reinstall GDAL pinned to the system library version
# so the Python bindings and libgdal.so are guaranteed to match.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --force-reinstall "GDAL==$(gdal-config --version)"

COPY . .

RUN python manage.py collectstatic --noinput

# Railway injects $PORT; fall back to 8000 for local docker run.
CMD python manage.py runserver 0.0.0.0:${PORT:-8000}

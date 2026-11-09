# CPU-only image: FastAPI service + ONNX Runtime detector + llama.cpp llama-server for the OCR.
# No torch (requirements.txt only). Models are NOT baked in: mount them at /app/models.
#
#   docker build -t manga-translate .
#   docker run -d --init --name manga-translate --env-file .env \
#     -v "$PWD/models:/app/models:ro" -p 127.0.0.1:8765:8765 manga-translate
#
# GGML_NATIVE=ON tunes llama.cpp for the CPU it is built on: build the image on the VPS itself
# (or pass --build-arg GGML_NATIVE=OFF for a portable, slower binary).

ARG PYTHON_VERSION=3.11

# ---- stage 1: llama-server (CPU) ----------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS llama
ARG LLAMA_CPP_TAG=b12189
ARG GGML_NATIVE=ON
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${LLAMA_CPP_TAG}" https://github.com/ggml-org/llama.cpp /src
WORKDIR /src
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
      -DGGML_NATIVE=${GGML_NATIVE} -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
 && cmake --build build --target llama-server -j"$(nproc)"

# ---- stage 2: runtime ---------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    # inside the container the service must listen on all interfaces; publish the port on 127.0.0.1 only
    HOST=0.0.0.0 PORT=8765
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --from=llama /src/build/bin/llama-server /app/tools/llama.cpp-cpu/llama-server
COPY *.py instructions.txt glossary.txt characters.txt .env.example ./
COPY tools/*.py tools/

RUN useradd --system --uid 10001 --home-dir /app app \
 && mkdir -p /app/logs /app/models && chown -R app:app /app/logs
USER app

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD python -c "import json,urllib.request as u; exit(json.load(u.urlopen('http://127.0.0.1:8765/health'))['status']!='ok')"
CMD ["python", "app.py"]

FROM python:3.11-slim AS build-stage

WORKDIR /function

COPY requirements.txt .

RUN pip install \
    --target /python \
    --no-cache-dir \
    -r requirements.txt

FROM python:3.11-slim

WORKDIR /function

COPY --from=build-stage /python /python
COPY func.py .

ENV PYTHONPATH=/python
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]

FROM python:3.9-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV MPLBACKEND=Agg
ENV PYTHONUNBUFFERED=1

WORKDIR /function

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ make \
    libfreetype6-dev libpng-dev zlib1g-dev libjpeg-dev \
    libx11-6 libxext6 libxrender1 fontconfig curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip && pip install fdk

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY func.py /function/func.py
COPY . /function

CMD ["fdk", "/function/func.py", "handler"]

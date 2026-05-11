FROM python:3.9-slim

USER root

RUN apt-get update && apt-get install -y poppler-utils

WORKDIR /function

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /function/

CMD ["fdk", "/function/func.py", "handler"]

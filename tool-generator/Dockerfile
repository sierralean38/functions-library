FROM fnproject/python:3.11-dev AS build-stage

WORKDIR /function

ADD requirements.txt /function/
RUN pip3 install --target /python/ --no-cache --no-cache-dir -r requirements.txt && \
    rm -fr ~/.cache/pip /tmp* requirements.txt func.yaml Dockerfile .venv && \
    chmod -R o+r /python

ADD . /function/
RUN rm -fr /function/.pip_cache /function/__pycache__

FROM fnproject/python:3.11

WORKDIR /function

COPY --from=build-stage /python /python
COPY --from=build-stage /function /function
RUN chmod -R o+r /function

ENV PYTHONPATH=/function:/python
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]

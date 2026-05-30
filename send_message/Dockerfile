FROM python:3.12-alpine AS test

WORKDIR /app

COPY app ./app
COPY tests ./tests

RUN python -m compileall app tests \
    && python -m unittest discover -s tests


FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1
ENV STATE_FILE=/data/state.json

WORKDIR /app

COPY --from=test /app/app ./app

RUN adduser -D -H appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

USER appuser

CMD ["python", "-m", "app.main"]

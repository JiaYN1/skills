FROM python:3.12-slim AS test

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests ./tests

RUN python -m compileall app tests \
    && python -m unittest discover -s tests


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV STATE_FILE=/data/state.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

COPY --from=test /app/app ./app

USER appuser

CMD ["python", "-m", "app.main"]

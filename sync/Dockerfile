FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cloudbuster_sync ./cloudbuster_sync

# Run as an unprivileged user. The state directory is a bind mount owned by this uid.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin cbsync
USER 10001

ENTRYPOINT ["python", "-m", "cloudbuster_sync"]

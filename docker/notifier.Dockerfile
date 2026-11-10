FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Time zone database for zoneinfo; the slim image has none.
RUN pip install --no-cache-dir tzdata==2025.2
WORKDIR /app
COPY notifier ./notifier
RUN useradd --create-home --uid 10002 notifier && mkdir /data && chown notifier:notifier /data
USER notifier
CMD ["python", "-m", "notifier.bot"]

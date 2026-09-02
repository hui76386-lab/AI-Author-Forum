FROM mcr.microsoft.com/playwright/python:v1.61.0-noble@sha256:a9731514f24121d1dcd25d58d0a38146646d290a5998fd80d3e533e7b5e21c69

ENV VIRTUAL_ENV=/venv \
    PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=ai_author_forum.settings.production \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu python3-venv fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && useradd wagtail --create-home \
    && mkdir /app /venv \
    && chown -R wagtail /app /venv
WORKDIR /app
USER wagtail

RUN python -m venv /venv
COPY --chown=wagtail:wagtail requirements.txt requirements-pdf.txt ./
RUN pip install --no-cache-dir -r requirements-pdf.txt

COPY --chown=wagtail . .

USER root
RUN chmod +x /app/docker/pdf-worker/entrypoint.sh
ENTRYPOINT ["/app/docker/pdf-worker/entrypoint.sh"]
CMD ["celery", "-A", "ai_author_forum", "worker", "--loglevel=INFO", "--concurrency=1", "--queues=reader_pdf"]

FROM python:3.14-slim as production

# Install dependencies in a virtualenv
ENV VIRTUAL_ENV=/venv

RUN useradd wagtail --create-home && mkdir /app $VIRTUAL_ENV && chown -R wagtail /app $VIRTUAL_ENV

WORKDIR /app

# Set default environment variables. They are used at build time and runtime.
# If you specify your own environment variables on Heroku or Dokku, they will
# override the ones set here. The ones below serve as sane defaults only.
#  * PATH - Make sure that Poetry is on the PATH, along with our venv
#  * PYTHONUNBUFFERED - This is useful so Python does not hold any messages
#    from being output.
#    https://docs.python.org/3.14/using/cmdline.html#envvar-PYTHONUNBUFFERED
#    https://docs.python.org/3.14/using/cmdline.html#cmdoption-u
#  * DJANGO_SETTINGS_MODULE - default settings used in the container.
#  * PORT - default port used. Please match with EXPOSE so it works on Dokku.
#    Heroku will ignore EXPOSE and only set PORT variable. PORT variable is
#    read/used by Gunicorn.
ENV PATH=$VIRTUAL_ENV/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=ai_author_forum.settings.production \
    PORT=8000

# Port exposed by this container. Should default to the port used by your WSGI
# server (Gunicorn).
EXPOSE 8000

RUN apt-get update --yes --quiet && apt-get install --yes --quiet --no-install-recommends \
    build-essential \
    gosu \
    && apt-get autoremove && rm -rf /var/lib/apt/lists/*

# Don't use the root user as it's an anti-pattern
USER wagtail

# Install your app's Python requirements.
RUN python -m venv $VIRTUAL_ENV
COPY requirements.txt ./
RUN pip install --no-cache -r requirements.txt

# Copy application code.
COPY --chown=wagtail . .

# Collect static. This command will move static files from application
# directories and "static_compiled" folder to the main static directory that
# will be served by the WSGI server.
# Production settings validate middleware URLs even though collectstatic does not
# connect to them. Use non-secret, local build-only values so image construction
# remains independent from deployment credentials.
RUN SECRET_KEY=none \
    MIDDLEWARE_MODE=local \
    DATABASE_URL=postgresql://build:build@database:5432/build \
    CACHE_BACKEND=django.core.cache.backends.redis.RedisCache \
    CACHE_LOCATION=redis://redis:6379/1 \
    CELERY_BROKER_URL=redis://redis:6379/0 \
    CELERY_RESULT_BACKEND=redis://redis:6379/0 \
    python manage.py collectstatic --noinput --clear

USER root
RUN chmod +x /app/docker/entrypoint.sh
ENTRYPOINT ["/app/docker/entrypoint.sh"]

# Runtime command that executes when "docker run" is called, it does the
# following:
#   1. Migrate the database.
#   2. Start the application server.
# WARNING:
#   Migrating database at the same time as starting the server IS NOT THE BEST
#   PRACTICE. The database should be migrated manually or using the release
#   phase facilities of your hosting platform. This is used only so the
#   Wagtail instance can be started with a simple "docker run" command.
CMD set -xe; python manage.py createcachetable; python manage.py migrate --noinput; gunicorn ai_author_forum.wsgi:application

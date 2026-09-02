"""
Django settings for AI Author Forum CMS.

For more information on this file, see
https://docs.djangoproject.com/en/stable/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/stable/ref/settings/
"""

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
import base64
import hashlib
import os

import dj_database_url

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = os.path.dirname(PROJECT_DIR)

SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-ai-author-forum-local")

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
SEO_NOINDEX = os.environ.get("SEO_NOINDEX", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

STATIC_PUBLISH_ENFORCE_CONTENT_READINESS = os.environ.get(
    "STATIC_PUBLISH_ENFORCE_CONTENT_READINESS", "false"
).strip().lower() in {"1", "true", "yes", "on"}

if "CSRF_TRUSTED_ORIGINS" in os.environ:
    CSRF_TRUSTED_ORIGINS = os.environ["CSRF_TRUSTED_ORIGINS"].split(",")

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/stable/howto/deployment/checklist/


# Application definition

INSTALLED_APPS = [
    "ai_author_forum.forms",
    "ai_author_forum.home",
    "ai_author_forum.images",
    "ai_author_forum.navigation",
    "ai_author_forum.news",
    "ai_author_forum.search",
    "ai_author_forum.standardpages",
    "ai_author_forum.users",
    "ai_author_forum.utils",
    "ai_author_forum.journals",
    "ai_author_forum.articles",
    "ai_author_forum.placements",
    "ai_author_forum.static_publish",
    "ai_author_forum.site_settings",
    "ai_author_forum.reader_access",
    "ai_author_forum.reader_interactions",
    "wagtail.contrib.table_block",
    "wagtail.contrib.settings",
    "wagtail.contrib.forms",
    "wagtail.contrib.redirects",
    "wagtail.sites",
    "wagtail.users",
    "wagtail.snippets",
    "wagtail.documents",
    "wagtail.images",
    "wagtail.search",
    "wagtail.admin",
    "wagtail.contrib.search_promotions",
    "wagtail",
    "modelcluster",
    "taggit",
    "django.contrib.admin",
    "django.contrib.postgres",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "ai_author_forum.reader_interactions.observability.ReaderObservabilityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "ai_author_forum.site_settings.middleware.AdminLocaleMiddleware",
    "ai_author_forum.site_settings.middleware.EnglishAdminResponseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "ai_author_forum.users.middleware.CredentialRateLimitMiddleware",
    "ai_author_forum.users.middleware.RequiredPasswordChangeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "ai_author_forum.site_settings.middleware.AdminNavigationPreviewFrameOptionsMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "wagtail.contrib.redirects.middleware.RedirectMiddleware",
]


ROOT_URLCONF = "ai_author_forum.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            os.path.join(BASE_DIR, "templates"),
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "wagtail.contrib.settings.context_processors.settings",
                "ai_author_forum.utils.context_processors.global_vars",
                "ai_author_forum.utils.context_processors.language_context",
                "ai_author_forum.utils.context_processors.site_frontend",
            ],
        },
    },
]

WSGI_APPLICATION = "ai_author_forum.wsgi.application"


# Database
# https://docs.djangoproject.com/en/stable/ref/settings/#databases

_DATABASE_CONN_MAX_AGE = int(os.environ.get("READER_DATABASE_CONN_MAX_AGE", "60"))
_DATABASE_STATEMENT_TIMEOUT_MS = int(
    os.environ.get("READER_DATABASE_STATEMENT_TIMEOUT_MS", "5000")
)
DATABASES = {
    "default": dj_database_url.config(
        default="sqlite:///" + os.path.join(BASE_DIR, "db.sqlite3"),
        conn_max_age=_DATABASE_CONN_MAX_AGE,
        conn_health_checks=True,
    ),
    "interactions": dj_database_url.parse(
        os.environ.get(
            "INTERACTIONS_DATABASE_URL",
            "sqlite:///" + os.path.join(BASE_DIR, "interactions.sqlite3"),
        ),
        conn_max_age=_DATABASE_CONN_MAX_AGE,
        conn_health_checks=True,
    ),
}
for _database in DATABASES.values():
    if _database["ENGINE"] == "django.db.backends.postgresql":
        _database.setdefault("OPTIONS", {}).setdefault(
            "options", f"-c statement_timeout={_DATABASE_STATEMENT_TIMEOUT_MS}"
        )

DATABASE_ROUTERS = [
    "ai_author_forum.reader_interactions.routers.ReaderInteractionsRouter"
]


# Password validation
# https://docs.djangoproject.com/en/stable/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/stable/topics/i18n/

LANGUAGE_CODE = os.environ.get("LANGUAGE_CODE", "zh-hans")
LANGUAGES = (
    ("zh-hans", "\u4e2d\u6587"),
    ("en", "English"),
)
LOCALE_PATHS = [os.path.join(BASE_DIR, "locale")]

TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Shanghai")

USE_I18N = True

USE_TZ = True

AUTH_USER_MODEL = "users.User"

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/stable/howto/static-files/

STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static_compiled"),
]

STATIC_ROOT = os.environ.get("STATIC_ROOT", os.path.join(BASE_DIR, "static"))
STATIC_URL = "/static/"

MEDIA_ROOT = os.environ.get("MEDIA_ROOT", os.path.join(BASE_DIR, "media"))
STATIC_PUBLISH_ROOT = os.environ.get(
    "STATIC_PUBLISH_ROOT", os.path.join(BASE_DIR, "published")
)
MEDIA_URL = "/media/"

# Default storage settings, with the staticfiles storage updated.
# See https://docs.djangoproject.com/en/stable/ref/settings/#std-setting-STORAGES
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    # ManifestStaticFilesStorage is recommended in production, to prevent
    # outdated JavaScript / CSS assets being served from cache
    # (e.g. after a Wagtail upgrade).
    # See https://docs.djangoproject.com/en/stable/ref/contrib/staticfiles/#manifeststaticfilesstorage
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
    },
}

CACHES = {
    "default": {
        "BACKEND": os.environ.get(
            "CACHE_BACKEND",
            "django.core.cache.backends.locmem.LocMemCache",
        ),
        "LOCATION": os.environ.get("CACHE_LOCATION", "ai-author-forum"),
    }
}


def get_first_env(*keys, default=None):
    """
    Return the first set environment variable (with a truthy value)
    """

    for key in keys:
        if val := os.getenv(key):
            return val

    return default


# Check for either DEFAULT_* (Divio) or AWS_* environment variables

# AWS_STORAGE_BUCKET_NAME: The name of the S3 bucket to use for storage.
AWS_STORAGE_BUCKET_NAME = get_first_env(
    "AWS_STORAGE_BUCKET_NAME", "DEFAULT_STORAGE_BUCKET"
)

# Only proceed if AWS_STORAGE_BUCKET_NAME is set
if AWS_STORAGE_BUCKET_NAME:
    # Add django-storages to the installed apps
    INSTALLED_APPS = INSTALLED_APPS + ["storages", "wagtail_storages"]

    # https://docs.djangoproject.com/en/stable/ref/settings/#std-setting-STORAGES
    STORAGES["default"]["BACKEND"] = "storages.backends.s3boto3.S3Boto3Storage"

    # AWS_ACCESS_KEY_ID: The access key ID for authenticating with AWS S3.
    AWS_ACCESS_KEY_ID = get_first_env(
        "AWS_ACCESS_KEY_ID", "DEFAULT_STORAGE_ACCESS_KEY_ID"
    )

    # AWS_SECRET_ACCESS_KEY: The secret access key for authenticating with AWS S3.
    AWS_SECRET_ACCESS_KEY = get_first_env(
        "AWS_SECRET_ACCESS_KEY", "DEFAULT_STORAGE_SECRET_ACCESS_KEY"
    )

    # We generally use this setting in the production to put the S3 bucket
    # behind a CDN using a custom domain, e.g. media.llamasavers.com.
    # https://django-storages.readthedocs.io/en/latest/backends/amazon-S3.html#cloudfront
    if custom_domain := get_first_env(
        "AWS_S3_CUSTOM_DOMAIN", "DEFAULT_STORAGE_CUSTOM_DOMAIN"
    ):
        AWS_S3_CUSTOM_DOMAIN = custom_domain

    # When signing URLs is facilitated, the region must be set, because the
    # global S3 endpoint does not seem to support that. Set this only if
    # necessary.
    if region_name := get_first_env("AWS_S3_REGION_NAME", "DEFAULT_STORAGE_REGION"):
        AWS_S3_REGION_NAME = region_name

    # Customize the endpoint, for non-AWS environments
    if endpoint_url := os.environ.get("AWS_S3_ENDPOINT_URL", None):
        AWS_S3_ENDPOINT_URL = endpoint_url

    # This settings lets you force using http or https protocol when generating
    # the URLs to the files. Set https as default.
    # https://github.com/jschneier/django-storages/blob/10d1929de5e0318dbd63d715db4bebc9a42257b5/storages/backends/s3boto3.py#L217
    AWS_S3_URL_PROTOCOL = os.environ.get("AWS_S3_URL_PROTOCOL", "https:")

    # Disables signing of the S3 objects' URLs. When set to True it
    # will append authorization querystring to each URL.
    AWS_QUERYSTRING_AUTH = False

    # Do not allow overriding files on S3 as per Wagtail docs recommendation:
    # https://docs.wagtail.io/en/stable/advanced_topics/deploying.html#cloud-storage
    # Not having this setting may have consequences in losing files.
    AWS_S3_FILE_OVERWRITE = False

    # Make uploaded files public. If this is not desirable, this should be changed to
    # "private" and protected using a bucket policy or wagtail-storages.
    AWS_DEFAULT_ACL = "public-read"

    # Limit how large a file can be spooled into memory before it's written to disk.
    AWS_S3_MAX_MEMORY_SIZE = 2 * 1024 * 1024  # 2MB

# Django sets a maximum of 1000 fields per form by default, but particularly complex page models
# can exceed this limit within Wagtail's page editor.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10_000


# Wagtail settings

WAGTAIL_SITE_NAME = os.environ.get("WAGTAIL_SITE_NAME", "AI Author Forum CMS")
WAGTAIL_ENABLE_UPDATE_CHECK = False

# Search
# https://docs.wagtail.org/en/stable/topics/search/backends.html
WAGTAILSEARCH_BACKENDS = {
    "default": {
        "BACKEND": "wagtail.search.backends.database",
    }
}

# Base URL to use when referring to full URLs within the Wagtail admin backend -
# e.g. in notification emails. Don't include '/admin' or a trailing slash
WAGTAILADMIN_BASE_URL = os.environ.get("WAGTAILADMIN_BASE_URL", "http://localhost:8000")
WAGTAILADMIN_NOTIFICATION_INCLUDE_SUPERUSERS = False

# Custom image model
# https://docs.wagtail.io/en/stable/advanced_topics/images/custom_image_model.html
WAGTAILIMAGES_IMAGE_MODEL = "images.CustomImage"
WAGTAILIMAGES_FEATURE_DETECTION_ENABLED = False

# Pagination
DEFAULT_PER_PAGE = 8

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_reader_bearer_paths": {
            "()": "ai_author_forum.settings.log_filters.RedactReaderBearerPaths"
        }
    },
    "handlers": {
        # Send logs with at least INFO level to the console.
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["redact_reader_bearer_paths"],
        },
    },
    "formatters": {
        "json": {"()": "ai_author_forum.settings.log_filters.JsonPrivacyFormatter"}
    },
    "loggers": {
        "ai_author_forum": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "wagtail": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

CACHE_CONTROL_S_MAXAGE = int(os.environ.get("CACHE_CONTROL_S_MAXAGE", 600))

CACHE_CONTROL_STALE_WHILE_REVALIDATE = int(
    os.environ.get("CACHE_CONTROL_STALE_WHILE_REVALIDATE", 30)
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Static publishing. Releases are built outside the web root and atomically
# promoted into ``current`` only after every target passes validation.
STATIC_PUBLISH_TARGET_PROVIDER = os.environ.get(
    "STATIC_PUBLISH_TARGET_PROVIDER",
    "ai_author_forum.static_publish.providers.WagtailPageTargetProvider",
)
STATIC_PUBLISH_KEEP_RELEASES = int(os.environ.get("STATIC_PUBLISH_KEEP_RELEASES", 5))
# Placement changes are batched before their affected fixed HTML pages are rebuilt.
# Set to false only when publication must be manually controlled during maintenance.
STATIC_PUBLISH_AUTO_ON_PLACEMENT_CHANGE = (
    os.environ.get("STATIC_PUBLISH_AUTO_ON_PLACEMENT_CHANGE", "true").lower() == "true"
)
STATIC_PUBLISH_AUTO_DEBOUNCE_SECONDS = int(
    os.environ.get("STATIC_PUBLISH_AUTO_DEBOUNCE_SECONDS", "60")
)

# Placement workflow V2 is opt-in during the compatibility window.
PLACEMENTS_V2_ENABLED = os.environ.get("PLACEMENTS_V2_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SIMPLE_JOURNAL_RBAC_ENABLED = os.environ.get(
    "SIMPLE_JOURNAL_RBAC_ENABLED", "false"
).lower() in {"1", "true", "yes", "on"}
SIMPLE_JOURNAL_RBAC_SHADOW_MODE = os.environ.get(
    "SIMPLE_JOURNAL_RBAC_SHADOW_MODE", "true"
).lower() in {"1", "true", "yes", "on"}
PLACEMENTS_BATCH_MAX_ITEMS = int(os.environ.get("PLACEMENTS_BATCH_MAX_ITEMS", "100"))

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_DEFAULT_QUEUE = os.environ.get("CELERY_TASK_DEFAULT_QUEUE", "celery")
CELERY_TASK_ROUTES = {
    "ai_author_forum.static_publish.tasks.*": {"queue": "static_publish"},
    "ai_author_forum.reader_interactions.tasks.send_magic_link": {
        "queue": "reader_email"
    },
    "ai_author_forum.reader_interactions.tasks.cleanup_reader_security_records": {
        "queue": "reader_email"
    },
    "ai_author_forum.reader_interactions.tasks.refresh_comment_snapshot": {
        "queue": "reader_comments"
    },
    "ai_author_forum.reader_access.tasks.apply_capability_projection": {
        "queue": "reader_comments"
    },
    "ai_author_forum.reader_access.tasks.reconcile_capability_projections": {
        "queue": "reader_comments"
    },
    "ai_author_forum.reader_access.tasks.apply_moderation_command": {
        "queue": "reader_comments"
    },
    "ai_author_forum.reader_access.tasks.reconcile_moderation_commands": {
        "queue": "reader_comments"
    },
    "ai_author_forum.reader_access.tasks.render_pdf": {"queue": "reader_pdf"},
}
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = int(os.environ.get("CELERY_TASK_TIME_LIMIT", 3600))
STATIC_PUBLISH_HEALTHCHECK_BROKER = (
    os.environ.get("STATIC_PUBLISH_HEALTHCHECK_BROKER", "false").lower() == "true"
)
STATIC_PUBLISH_BROKER_HEALTHCHECK_TIMEOUT = float(
    os.environ.get("STATIC_PUBLISH_BROKER_HEALTHCHECK_TIMEOUT", "2")
)

# Reader-facing capabilities remain fail-closed until their implementation card
# has passed its own acceptance gate and an operator explicitly enables it.
READER_INTERACTIONS_ENABLED = os.environ.get(
    "READER_INTERACTIONS_ENABLED", "false"
).lower() in {"1", "true", "yes", "on"}
READER_EMAIL_VERIFICATION_ENABLED = os.environ.get(
    "READER_EMAIL_VERIFICATION_ENABLED", "false"
).lower() in {"1", "true", "yes", "on"}
READER_COMMENTS_WRITE_ENABLED = os.environ.get(
    "READER_COMMENTS_WRITE_ENABLED", "false"
).lower() in {"1", "true", "yes", "on"}
READER_PDF_GRANTS_ENABLED = os.environ.get(
    "READER_PDF_GRANTS_ENABLED", "false"
).lower() in {"1", "true", "yes", "on"}
READER_SHARE_UI_ENABLED = os.environ.get(
    "READER_SHARE_UI_ENABLED", "false"
).lower() in {"1", "true", "yes", "on"}
READER_SNAPSHOT_READ_FALLBACK = os.environ.get(
    "READER_SNAPSHOT_READ_FALLBACK", "false"
).lower() in {"1", "true", "yes", "on"}
READER_COMMENT_RISK_ENABLED = os.environ.get(
    "READER_COMMENT_RISK_ENABLED", "true"
).lower() in {"1", "true", "yes", "on"}
READER_COMMENT_RISK_TIMEOUT_SECONDS = float(
    os.environ.get("READER_COMMENT_RISK_TIMEOUT_SECONDS", "1")
)
READER_COMMENT_MAX_CHARS = int(os.environ.get("READER_COMMENT_MAX_CHARS", "2000"))
READER_COMMENT_MAX_BYTES = int(os.environ.get("READER_COMMENT_MAX_BYTES", "8000"))
READER_COMMENT_PAGE_SIZE = int(os.environ.get("READER_COMMENT_PAGE_SIZE", "20"))
READER_COMMENT_CACHE_SECONDS = int(os.environ.get("READER_COMMENT_CACHE_SECONDS", "30"))
READER_COMMENT_INTERVAL_SECONDS = int(
    os.environ.get("READER_COMMENT_INTERVAL_SECONDS", "10")
)
READER_COMMENT_HOURLY_LIMIT = int(os.environ.get("READER_COMMENT_HOURLY_LIMIT", "20"))
READER_COMMENT_DAILY_LIMIT = int(os.environ.get("READER_COMMENT_DAILY_LIMIT", "100"))
READER_COMMENT_ARTICLE_HOURLY_LIMIT = int(
    os.environ.get("READER_COMMENT_ARTICLE_HOURLY_LIMIT", "10")
)
READER_COMMENT_IP_HOURLY_LIMIT = int(
    os.environ.get("READER_COMMENT_IP_HOURLY_LIMIT", "60")
)
READER_REPORT_DAILY_LIMIT = int(os.environ.get("READER_REPORT_DAILY_LIMIT", "20"))
READER_REPORT_IP_DAILY_LIMIT = int(
    os.environ.get("READER_REPORT_IP_DAILY_LIMIT", "100")
)
READER_COMMENT_SNAPSHOT_ROOT = os.environ.get(
    "READER_COMMENT_SNAPSHOT_ROOT", os.path.join(BASE_DIR, "comment_snapshots")
)
READER_PRIVATE_STORAGE_BACKEND = (
    os.environ.get("READER_PRIVATE_STORAGE_BACKEND", "filesystem").strip().lower()
)
READER_PRIVATE_STORAGE_ROOT = os.environ.get(
    "READER_PRIVATE_STORAGE_ROOT", os.path.join(BASE_DIR, "protected_pdfs")
)
READER_PDF_MAX_BYTES = int(
    os.environ.get("READER_PDF_MAX_BYTES", str(100 * 1024 * 1024))
)
READER_PDF_RENDER_TIMEOUT_SECONDS = int(
    os.environ.get("READER_PDF_RENDER_TIMEOUT_SECONDS", "60")
)
READER_PDF_RENDERER_VERSION = os.environ.get(
    "READER_PDF_RENDERER_VERSION", "playwright-python/1.61.0-chromium"
)
READER_PDF_BUILD_WAIT_SECONDS = int(
    os.environ.get("READER_PDF_BUILD_WAIT_SECONDS", "900")
)
READER_PDF_BUILD_POLL_SECONDS = float(
    os.environ.get("READER_PDF_BUILD_POLL_SECONDS", "0.5")
)
READER_DOWNLOAD_GRANT_TTL_SECONDS = int(
    os.environ.get("READER_DOWNLOAD_GRANT_TTL_SECONDS", "300")
)
READER_PDF_GRANT_REDIS_URL = os.environ.get(
    "READER_PDF_GRANT_REDIS_URL",
    os.environ.get("READER_RATE_LIMIT_REDIS_URL")
    or os.environ.get("CACHE_LOCATION", ""),
)
READER_DOWNLOAD_ARTICLE_HOURLY_LIMIT = int(
    os.environ.get("READER_DOWNLOAD_ARTICLE_HOURLY_LIMIT", "5")
)
READER_DOWNLOAD_DAILY_LIMIT = int(os.environ.get("READER_DOWNLOAD_DAILY_LIMIT", "50"))
READER_DOWNLOAD_IP_HOURLY_LIMIT = int(
    os.environ.get("READER_DOWNLOAD_IP_HOURLY_LIMIT", "20")
)
READER_PDF_X_ACCEL_PREFIX = os.environ.get(
    "READER_PDF_X_ACCEL_PREFIX", "/_protected_pdf/"
)
READER_S3_BUCKET = os.environ.get("READER_S3_BUCKET", "")
READER_S3_ENDPOINT_URL = os.environ.get("READER_S3_ENDPOINT_URL", "")
READER_S3_REGION = os.environ.get("READER_S3_REGION", "")
READER_S3_ACCESS_KEY_ID = os.environ.get("READER_S3_ACCESS_KEY_ID", "")
READER_S3_SECRET_ACCESS_KEY = os.environ.get("READER_S3_SECRET_ACCESS_KEY", "")


def _derived_reader_secret(label):
    return hashlib.sha256(f"{SECRET_KEY}:{label}".encode()).hexdigest()


def _derived_fernet_key(label):
    digest = hashlib.sha256(f"{SECRET_KEY}:{label}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode()


# Development derives isolated values from its local SECRET_KEY. Production
# validates that explicit secret-manager values are present before reader
# identity features can be enabled.
READER_SESSION_COOKIE_NAME = os.environ.get(
    "READER_SESSION_COOKIE_NAME", "reader_session"
)
READER_SESSION_ABSOLUTE_SECONDS = int(
    os.environ.get("READER_SESSION_ABSOLUTE_SECONDS", str(30 * 24 * 60 * 60))
)
READER_SESSION_IDLE_SECONDS = int(
    os.environ.get("READER_SESSION_IDLE_SECONDS", str(14 * 24 * 60 * 60))
)
READER_SESSION_TOUCH_INTERVAL_SECONDS = int(
    os.environ.get("READER_SESSION_TOUCH_INTERVAL_SECONDS", "300")
)
READER_TRUST_PROXY_CLIENT_IP = os.environ.get(
    "READER_TRUST_PROXY_CLIENT_IP", "false"
).lower() in {"1", "true", "yes", "on"}
READER_SESSION_COOKIE_SECURE = os.environ.get(
    "READER_SESSION_COOKIE_SECURE", "false"
).lower() in {"1", "true", "yes", "on"}
READER_EMAIL_LOOKUP_KEY = os.environ.get("READER_EMAIL_LOOKUP_KEY") or (
    _derived_reader_secret("reader-email-lookup")
)
READER_EMAIL_ENCRYPTION_KEYS = (
    os.environ.get("READER_EMAIL_ENCRYPTION_KEYS")
    or f"1:{_derived_fernet_key('reader-email-encryption')}"
)
READER_TOKEN_PEPPER = os.environ.get("READER_TOKEN_PEPPER") or (
    _derived_reader_secret("reader-token-pepper")
)
READER_MAGIC_LINK_TTL_SECONDS = int(
    os.environ.get("READER_MAGIC_LINK_TTL_SECONDS", "900")
)
READER_DEVICE_FLOW_TTL_SECONDS = int(
    os.environ.get("READER_DEVICE_FLOW_TTL_SECONDS", "900")
)
READER_DEVICE_FLOW_POLL_INTERVAL_SECONDS = int(
    os.environ.get("READER_DEVICE_FLOW_POLL_INTERVAL_SECONDS", "5")
)
READER_DEVICE_FLOW_ATTEMPT_LIMIT = int(
    os.environ.get("READER_DEVICE_FLOW_ATTEMPT_LIMIT", "5")
)
READER_DEVICE_FLOW_STATUS_LIMIT = int(
    os.environ.get("READER_DEVICE_FLOW_STATUS_LIMIT", "120")
)
READER_DEVICE_FLOW_CLAIM_LIMIT = int(
    os.environ.get("READER_DEVICE_FLOW_CLAIM_LIMIT", "10")
)
READER_DEVICE_FLOW_STATUS_WINDOW_SECONDS = int(
    os.environ.get("READER_DEVICE_FLOW_STATUS_WINDOW_SECONDS", "60")
)
READER_DEVICE_FLOW_COOKIE_NAME = os.environ.get(
    "READER_DEVICE_FLOW_COOKIE_NAME", "reader_device_flow"
)
READER_DEVICE_FLOW_COOKIE_SECURE = os.environ.get(
    "READER_DEVICE_FLOW_COOKIE_SECURE",
    os.environ.get("READER_SESSION_COOKIE_SECURE", "false"),
).lower() in {"1", "true", "yes", "on"}
READER_DEVICE_FLOW_COOKIE_MAX_AGE = int(
    os.environ.get("READER_DEVICE_FLOW_COOKIE_MAX_AGE", "900")
)
READER_PUBLIC_BASE_URL = os.environ.get(
    "READER_PUBLIC_BASE_URL", WAGTAILADMIN_BASE_URL
).rstrip("/")
READER_EMAIL_FROM = os.environ.get("READER_EMAIL_FROM", "reader-security@localhost")
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
EMAIL_TIMEOUT = float(os.environ.get("EMAIL_TIMEOUT", "10"))
READER_RATE_LIMIT_REDIS_URL = os.environ.get(
    "READER_RATE_LIMIT_REDIS_URL"
) or os.environ.get("CACHE_LOCATION", "")
READER_CAPABILITY_REDIS_URL = (
    os.environ.get("READER_CAPABILITY_REDIS_URL") or READER_RATE_LIMIT_REDIS_URL
)
READER_COMMENT_CACHE_REDIS_URL = (
    os.environ.get("READER_COMMENT_CACHE_REDIS_URL") or READER_CAPABILITY_REDIS_URL
)
READER_INTERNAL_SERVICE_TOKEN = os.environ.get("READER_INTERNAL_SERVICE_TOKEN", "")
READER_VERIFICATION_IP_LIMIT = int(os.environ.get("READER_VERIFICATION_IP_LIMIT", "5"))
READER_VERIFICATION_EMAIL_LIMIT = int(
    os.environ.get("READER_VERIFICATION_EMAIL_LIMIT", "3")
)
READER_VERIFICATION_GLOBAL_LIMIT = int(
    os.environ.get("READER_VERIFICATION_GLOBAL_LIMIT", "1000")
)
READER_VERIFICATION_WINDOW_SECONDS = int(
    os.environ.get("READER_VERIFICATION_WINDOW_SECONDS", "3600")
)
READER_VERIFICATION_CONSUME_LIMIT = int(
    os.environ.get("READER_VERIFICATION_CONSUME_LIMIT", "5")
)
READER_SECURITY_RETENTION_DAYS = int(
    os.environ.get("READER_SECURITY_RETENTION_DAYS", "30")
)
READER_INTERACTIONS_DB_CONNECTION_LIMIT = int(
    os.environ.get("READER_INTERACTIONS_DB_CONNECTION_LIMIT", "20")
)

# Article DOCX/Markdown import safety and capacity limits.
ARTICLE_DOCUMENT_IMPORT_ENABLED = os.environ.get(
    "ARTICLE_DOCUMENT_IMPORT_ENABLED", "true"
).lower() in {"1", "true", "yes", "on"}
ARTICLE_IMPORT_MAX_DOCUMENTS_PER_JOB = int(
    os.environ.get("ARTICLE_IMPORT_MAX_DOCUMENTS_PER_JOB", "200")
)
ARTICLE_IMPORT_MAX_DOCX_SIZE = int(
    os.environ.get("ARTICLE_IMPORT_MAX_DOCX_SIZE", str(25 * 1024 * 1024))
)
ARTICLE_IMPORT_MAX_MARKDOWN_SIZE = int(
    os.environ.get("ARTICLE_IMPORT_MAX_MARKDOWN_SIZE", str(5 * 1024 * 1024))
)
ARTICLE_IMPORT_MAX_CONVERTED_HTML_SIZE = int(
    os.environ.get("ARTICLE_IMPORT_MAX_CONVERTED_HTML_SIZE", str(10 * 1024 * 1024))
)
ARTICLE_IMPORT_MAX_LOGICAL_EXTRACTED_SIZE = int(
    os.environ.get("ARTICLE_IMPORT_MAX_LOGICAL_EXTRACTED_SIZE", str(250 * 1024 * 1024))
)
ARTICLE_IMPORT_MAX_DOCX_MEMBERS = int(
    os.environ.get("ARTICLE_IMPORT_MAX_DOCX_MEMBERS", "2000")
)
ARTICLE_IMPORT_MAX_NESTED_MEMBERS_PER_JOB = int(
    os.environ.get("ARTICLE_IMPORT_MAX_NESTED_MEMBERS_PER_JOB", "20000")
)
ARTICLE_IMPORT_MAX_XML_NODES = int(
    os.environ.get("ARTICLE_IMPORT_MAX_XML_NODES", "200000")
)
ARTICLE_IMPORT_MAX_XML_DEPTH = int(
    os.environ.get("ARTICLE_IMPORT_MAX_XML_DEPTH", "128")
)
ARTICLE_IMPORT_MAX_MARKDOWN_LINES = int(
    os.environ.get("ARTICLE_IMPORT_MAX_MARKDOWN_LINES", "200000")
)
ARTICLE_IMPORT_MAX_MARKDOWN_CHARACTERS = int(
    os.environ.get("ARTICLE_IMPORT_MAX_MARKDOWN_CHARACTERS", "900000")
)
ARTICLE_IMPORT_MARKDOWN_CHARACTERS_WARNING = int(
    os.environ.get("ARTICLE_IMPORT_MARKDOWN_CHARACTERS_WARNING", "800000")
)
ARTICLE_IMPORT_VISIBLE_CHARACTERS_WARNING = int(
    os.environ.get("ARTICLE_IMPORT_VISIBLE_CHARACTERS_WARNING", "100000")
)
ARTICLE_IMPORT_MAX_VISIBLE_CHARACTERS = int(
    os.environ.get("ARTICLE_IMPORT_MAX_VISIBLE_CHARACTERS", "1000000")
)
ARTICLE_IMPORT_MAX_TOTAL_IMAGE_PIXELS = int(
    os.environ.get("ARTICLE_IMPORT_MAX_TOTAL_IMAGE_PIXELS", "250000000")
)
ARTICLE_IMPORT_PREVIEW_TIMEOUT_SECONDS = int(
    os.environ.get("ARTICLE_IMPORT_PREVIEW_TIMEOUT_SECONDS", "600")
)
ARTICLE_IMPORT_MAX_CONCURRENT_PREVIEWS_PER_USER = int(
    os.environ.get("ARTICLE_IMPORT_MAX_CONCURRENT_PREVIEWS_PER_USER", "2")
)

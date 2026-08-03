# Static publishing operations

The `static_publish` application builds public pages into immutable releases. A
release is rendered in a staging directory, checked for missing local assets,
and promoted to `STATIC_PUBLISH_ROOT/current` only after all pages succeed.
The public web server should serve that `current` directory and must not route
article requests back to Django.

## Commands

Build the complete site:

```console
python manage.py build_static_site
```

Rebuild only affected URLs:

```console
python manage.py build_static_site --path / --path /journals/example/
```

Retry failed pages from a job or activate an earlier release:

```console
python manage.py build_static_site --retry-job 42
python manage.py build_static_site --rollback 20260718T120000000000Z-job41 --rollback-reason "Restore the verified stable release"
```

The same operations are available under **Static publishing** in Wagtail admin.
Publishing and rollback require the `static_publish.publish_static_site`
permission. Viewing reports requires `static_publish.view_staticpublishjob`.

## Import and publish

Import a journal package and create a centralized full-site publish job after the
import succeeds:

```console
python manage.py import_journal_package \
  --package package.zip \
  --publish-static-site \
  --operator-id 1
```

`--operator-id` associates both the import audit events and the publish job with
the Wagtail user who initiated the operation. A dry run validates the package
but does not create a `StaticPublishJob`.

Import-triggered publishing uses the same `StaticPublisher` pipeline as the
publishing center. Releases are written under `STATIC_PUBLISH_ROOT/releases`,
the active release is promoted to `STATIC_PUBLISH_ROOT/current`, and every job
records its manifest, page-level results, failures, and audit events. The import
job summary stores the related static publish job ID, status, and version.

Import-time custom output directories are intentionally unsupported. The legacy
`--static-output-dir` and `--clear-static-output` options now fail with an
instruction to use `--publish-static-site`, so retry and rollback always operate
on centrally managed releases. Import-triggered jobs appear in **Static
publishing** and use the normal retry and rollback controls.

For repeatable 120-journal acceptance data, generate and import a deterministic
package through the same workflow:

```console
python manage.py seed_journal_demo_data --journals 120 --articles-per-journal 100
python manage.py seed_journal_demo_data --journals 120 --articles-per-journal 125
```

The first command covers the formal 120 x 100 requirement. The second covers a
15,000 article channel test. Imported AI Article details are fixed static HTML
sources; public article pages are generated into static files and must not be
served by a runtime article-detail query path.

## Integration contract

`STATIC_PUBLISH_TARGET_PROVIDER` is a dotted Python path to a class exposing
`get_targets(paths=None)`. Each returned target provides `output_path`,
`source`, and `render() -> bytes`. The default composite provider publishes
live non-article Wagtail pages plus the formal project outputs:

- `/journals/index.html`
- `/journals/{journal_slug}/index.html`
- `/explore-content/{section_slug}/index.html`
- `/articles/{article_slug}/index.html` for effectively placed articles only
- `/search/index.html` as a static recommendation page

`manifest.json` is written by `StaticPublisher` after every target succeeds; it
is not a separately rendered target. The public Search experience is static at
`/search/`; no database-backed search route is exposed. A custom provider may
still replace the default without changing the build, manifest, retry, or
rollback logic.

Use Celery workers for admin-triggered builds:

```console
celery -A ai_author_forum worker --loglevel=INFO
```

Full publishes, failed-page retries, and rollbacks are queued through Celery so
Wagtail admin requests do not block while files are rendered or switched.

Set `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `STATIC_PUBLISH_ROOT`, and
`STATIC_PUBLISH_KEEP_RELEASES` per environment. Only one publisher may operate
on a root at a time; a file lock rejects concurrent builds.

## Article publication state

`ArticlePage.review_status` remains the content-moderation result. Static delivery
is tracked independently in `ArticlePage.publication_status`:

- `APPROVED`: moderation passed, but no currently effective placement exists.
- `PLACED`: at least one effective `ArticlePlacement` makes the article eligible
  for the next static build.
- `BUILT`: the article target rendered successfully for `build_version`, but
  that candidate release is not the active release.
- `PUBLISHED`: the active manifest contains the canonical article output;
  `published_version` identifies that active release.
- `OFFLINE`: the article entered the delivery lifecycle previously, but the
  active manifest no longer contains it.

A failed article target keeps `publish_failure_reason` and does not replace the
active release. Successful activation and rollback both rescan the selected
manifest, so article state is based on files actually present in the active
release rather than the requested job scope.

`ArticlePage` is authoritative. A legacy `StaticArticle` status is mapped into
the separate review/publication fields only when its canonical page is first
created or during the data migration. Later publication changes are mirrored
back to `StaticArticle.review_status` and `build_version` for compatibility;
legacy rows do not independently control active publication state.

## Acceptance

With the Python virtual environment active, run:

```console
npm run test:e2e
```

The preparation step uses an isolated `.e2e/db.sqlite3` database and media
folder, creates approved and placed acceptance content, builds two complete
static releases, and rolls back to the first release. Playwright then serves the
active `static_publish_output/current` directory and verifies the home page,
A-Z directory, journal homepage, article detail, section, and static Search
page. It also rejects failed local image, CSS, JavaScript, or font requests,
checks the manifest page/failure counts and media-reference index, and confirms
that the rolled-back section content and manifest version are active. Fixed
viewport screenshots provide visual regression coverage for every formal page.

Use `npm run test:e2e:only` to rerun the browser checks against the already
prepared static output without recreating the isolated data.

init: load-data start

start:
	python scripts/start_dev.py

django:
	python ./manage.py runserver

static:
	python -m ai_author_forum.static_publish.static_server --root ./published --host 127.0.0.1 --port 4173

load-data:
	python ./manage.py dev_setup
	python ./manage.py seed_navigation
	python ./manage.py seed_roles

dump-data:
	python ./manage.py dumpdata --natural-foreign --indent 2 -e auth.permission -e contenttypes -e wagtailcore.GroupCollectionPermission -e wagtailimages.rendition -e images.rendition -e sessions -e wagtailsearch.indexentry -e wagtailsearch.sqliteftsindexentry -e wagtailcore.referenceindex -e wagtailcore.pagesubscription > fixtures/demo.json

test:
	python ./manage.py check
	python ./manage.py test

foundation-check:
	python ./manage.py makemigrations --check --dry-run
	python ./manage.py check
	python ./manage.py seed_navigation
	python ./manage.py seed_roles
	python -m pytest ai_author_forum/site_settings/tests -q
	ruff check ai_author_forum/site_settings ai_author_forum/articles ai_author_forum/journals ai_author_forum/placements ai_author_forum/static_publish
	black --check ai_author_forum/site_settings ai_author_forum/articles ai_author_forum/journals ai_author_forum/placements ai_author_forum/static_publish
	isort --check-only ai_author_forum/site_settings ai_author_forum/articles ai_author_forum/journals ai_author_forum/placements ai_author_forum/static_publish

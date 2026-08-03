from django.views.static import serve

MARKDOWN_EXTENSIONS = {".md", ".markdown"}


def serve_media(request, path, document_root=None, show_indexes=False):
    """Serve local media with a browser-safe charset for Markdown documents."""
    response = serve(
        request,
        path,
        document_root=document_root,
        show_indexes=show_indexes,
    )
    if (
        path.lower().endswith(tuple(MARKDOWN_EXTENSIONS))
        and response.status_code == 200
    ):
        response["Content-Type"] = "text/markdown; charset=utf-8"
    return response

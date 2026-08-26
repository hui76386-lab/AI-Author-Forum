from pathlib import Path

NGINX_CONFIG = (
    Path(__file__).resolve().parents[1] / "docker" / "nginx.conf"
).read_text(encoding="utf-8")


def test_nginx_uses_sendfile_and_release_gated_try_files():
    assert "sendfile on;" in NGINX_CONFIG
    assert "root /srv/published/current;" in NGINX_CONFIG
    assert "if (-f /srv/published/current/.nginx-direct-ready)" in NGINX_CONFIG
    assert (
        "try_files $static_candidate/index.html $static_candidate @static_frontend;"
        in (NGINX_CONFIG)
    )
    assert "add_header X-Static-Served-By nginx always;" in NGINX_CONFIG


def test_static_pages_use_a_same_origin_content_security_policy():
    start = NGINX_CONFIG.index("location / {")
    end = NGINX_CONFIG.index("\n    }", start)
    block = NGINX_CONFIG[start:end]

    assert "add_header Content-Security-Policy" in block
    assert "script-src 'self'" in block
    assert "connect-src 'self'" in block
    assert "object-src 'none'" in block


def test_nginx_redirect_sentinels_fall_back_to_manifest_service():
    assert "error_page 418 = @static_frontend;" in NGINX_CONFIG
    assert (
        "if (-f /srv/published/current/.nginx-redirects$uri/index.html.redirect)"
        in NGINX_CONFIG
    )
    assert "location @static_frontend" in NGINX_CONFIG
    assert "add_header X-Static-Served-By static-frontend always;" in NGINX_CONFIG


def test_nginx_never_serves_internal_release_metadata():
    assert "location = /.nginx-direct-ready" in NGINX_CONFIG
    assert "location ^~ /.nginx-redirects/" in NGINX_CONFIG


def test_nginx_static_health_always_uses_the_static_frontend():
    start = NGINX_CONFIG.index("location = /__static_health__/ {")
    end = NGINX_CONFIG.index("\n    }", start)
    block = NGINX_CONFIG[start:end]

    assert "proxy_pass http://$static_frontend;" in block
    assert "X-Static-Served-By static-frontend" in block


def test_reader_api_logs_omit_query_referer_and_user_agent():
    log_start = NGINX_CONFIG.index("log_format reader_api")
    log_end = NGINX_CONFIG.index(";", log_start)
    log_format = NGINX_CONFIG[log_start:log_end]
    assert "$uri" in log_format
    assert "$request_uri" not in log_format
    assert "$http_referer" not in log_format
    assert "$http_user_agent" not in log_format


def test_reader_api_ingress_overwrites_forwarded_client_address():
    start = NGINX_CONFIG.index("location ^~ /reader-api/ {")
    end = NGINX_CONFIG.index("\n    }", start)
    block = NGINX_CONFIG[start:end]
    assert "proxy_set_header X-Real-IP $remote_addr;" in block
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in block
    assert "$proxy_add_x_forwarded_for" not in block


def test_protected_pdf_is_internal_and_download_tokens_are_not_logged():
    download_start = NGINX_CONFIG.index("location ^~ /reader-api/v1/downloads/ {")
    download_end = NGINX_CONFIG.index("\n    }", download_start)
    download_block = NGINX_CONFIG[download_start:download_end]
    protected_start = NGINX_CONFIG.index("location ^~ /_protected_pdf/ {")
    protected_end = NGINX_CONFIG.index("\n    }", protected_start)
    protected_block = NGINX_CONFIG[protected_start:protected_end]

    assert "access_log off;" in download_block
    assert "internal;" in protected_block
    assert "alias /srv/protected-pdfs/;" in protected_block
    assert "sendfile on;" in protected_block


def test_reader_ingress_has_flood_connection_body_and_timeout_guards():
    assert "limit_req_zone $binary_remote_addr zone=reader_api_per_ip" in NGINX_CONFIG
    assert (
        "limit_req_zone $binary_remote_addr zone=reader_verify_per_ip" in NGINX_CONFIG
    )
    assert (
        "limit_conn_zone $binary_remote_addr zone=reader_connections_per_ip"
        in NGINX_CONFIG
    )
    assert "limit_req_status 429;" in NGINX_CONFIG
    assert "limit_conn_status 429;" in NGINX_CONFIG

    start = NGINX_CONFIG.index("location ^~ /reader-api/ {")
    end = NGINX_CONFIG.index("\n    }", start)
    block = NGINX_CONFIG[start:end]
    assert "client_max_body_size 64k;" in block
    assert "proxy_connect_timeout 2s;" in block
    assert "proxy_read_timeout 15s;" in block
    assert "limit_conn reader_connections_per_ip 20;" in block

    verification_start = NGINX_CONFIG.index(
        "location = /reader-api/v1/email-verifications/ {"
    )
    verification_end = NGINX_CONFIG.index("\n    }", verification_start)
    verification = NGINX_CONFIG[verification_start:verification_end]
    assert "limit_req zone=reader_verify_per_ip burst=3 nodelay;" in verification


def test_metrics_and_bearer_paths_are_not_access_logged():
    metrics_start = NGINX_CONFIG.index("location = /reader-api/internal/v1/metrics/ {")
    metrics_end = NGINX_CONFIG.index("\n    }", metrics_start)
    assert "access_log off;" in NGINX_CONFIG[metrics_start:metrics_end]


def test_sensitive_reader_paths_reject_encoded_traversal_before_normalization():
    assert "map $request_uri $reader_path_traversal" in NGINX_CONFIG
    assert "(?:\\.\\.|%2e|%2f|%5c)" in NGINX_CONFIG
    assert "if ($reader_path_traversal)" in NGINX_CONFIG
    assert "return 400;" in NGINX_CONFIG

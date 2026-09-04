import json

from ht_lead_radar.sanitization import (
    safe_error,
    sanitize_diagnostic_fields,
    sanitize_text,
    sanitize_tree,
    sanitize_url,
)
from ht_lead_radar.talent_pool import canonical_payload_hash


def test_safe_error_redacts_space_separated_keys_credentials_and_bare_tokens():
    for raw in (
        "RuntimeError: api key sk-supersecret123456",
        "credential=" + "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz",
        "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz",
        "supersecrettokenvalue",
    ):
        rendered = repr(safe_error(raw))
        assert "supersecret" not in rendered
        assert "abcdefghijklmnopqrstuvwxyz" not in rendered
    token = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz"
    assert safe_error(token)["error_class"] == "OperationalError"
    assert safe_error("supersecrettokenvalue")["error_class"] == "OperationalError"
    assert (
        safe_error("OpenClawBinaryUnavailable: binary missing")["error_class"]
        == "OpenClawBinaryUnavailable"
    )


def test_safe_error_preserves_bounded_operational_phrase_while_redacting_data():
    diagnostic = safe_error(
        TimeoutError(
            "source watchdog exceeded during listing; "
            "token=secret-value; contact marcus@example.com"
        )
    )

    assert diagnostic["error_class"] == "TimeoutError"
    assert "source watchdog exceeded during listing" in diagnostic["detail"]
    assert "secret-value" not in diagnostic["detail"]
    assert "marcus@example.com" not in diagnostic["detail"]


def test_business_payload_newlines_and_hash_survive_recursive_sanitization():
    payload = {
        "position_name": "商业化总监",
        "position_scope": "【岗位职责】\n• 建立商业化体系\n\n【任职要求】\n• 十年以上经验",
        "cities": ["上海"],
    }
    json_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    envelope = {
        "public_payload": payload,
        "liepin_payload": payload,
        "liepin_payload_json": json_bytes.decode("utf-8"),
        "payload_hash": canonical_payload_hash(payload),
        "diagnostic": "call 13800138000 token=outside-secret",
    }

    sanitized = sanitize_tree(envelope, redact_pii=True)

    assert sanitized["public_payload"] == payload
    assert sanitized["liepin_payload"] == payload
    assert sanitized["liepin_payload_json"].encode("utf-8") == json_bytes
    assert sanitized["payload_hash"] == canonical_payload_hash(payload)
    assert canonical_payload_hash(sanitized["public_payload"]) == (
        canonical_payload_hash(payload)
    )
    assert "13800138000" not in sanitized["diagnostic"]
    assert "outside-secret" not in sanitized["diagnostic"]


def test_recursive_sanitization_redacts_json_query_password_and_access_token():
    value = {
        "nested": {
            "access_token": "sensitive-a",
            "detail": "password=sensitive-b url=https://x.test/?access_token=sensitive-c",
        }
    }

    sanitized = sanitize_tree(value)

    rendered = repr(sanitized)
    assert "sensitive-a" not in rendered
    assert "sensitive-b" not in rendered
    assert "sensitive-c" not in rendered
    assert sanitized["nested"]["access_token"] == "[redacted]"


def test_safe_error_redacts_common_chinese_pii_without_changing_business_tree():
    detail = safe_error(
        "contact marcus@example.com +86 138-0013-8000 id=11010519491231002X"
    )["detail"]

    assert "marcus@example.com" not in detail
    assert "138-0013-8000" not in detail
    assert "11010519491231002X" not in detail
    business = {"position_scope": "联系行业客户并建立销售体系\n• 不包含个人联系方式"}
    assert sanitize_tree(business) == business


def test_tree_pii_mode_is_explicit_and_does_not_change_default_payload_hash():
    payload = {"position_scope": "contact marcus@example.com or 13800138000"}
    assert sanitize_tree(payload) == payload
    assert canonical_payload_hash(sanitize_tree(payload)) == canonical_payload_hash(payload)
    operational = sanitize_tree(payload, redact_pii=True)
    assert "marcus@example.com" not in operational["position_scope"]
    assert "13800138000" not in operational["position_scope"]


def test_common_http_credential_headers_are_fully_redacted():
    secret_values = {
        "basic-user-pass",
        "session-secret",
        "csrf-secret",
        "response-cookie",
        "header-api-secret",
    }
    diagnostic = (
        "request failed\n"
        "Authorization: Basic basic-user-pass\n"
        "Cookie: session=session-secret; csrf=csrf-secret\n"
        "Set-Cookie: sid=response-cookie; Path=/; HttpOnly\n"
        "X-Api-Key: header-api-secret\n"
        "status=401"
    )

    rendered = sanitize_text(diagnostic, limit=2000)
    preserved = sanitize_tree({"error": diagnostic})["error"]

    for secret in secret_values:
        assert secret not in rendered
        assert secret not in preserved
    assert rendered.count("[redacted]") >= 4
    assert "status=401" in rendered


def test_diagnostic_field_sanitizer_preserves_public_payload_and_hash():
    payload = {
        "public_payload": {
            "position_name": "安全与认证商业化总监",
            "position_scope": "负责 Authorization 与 Cookie 产品能力的商业化",
        },
        "trace": ["Set-Cookie: sid=trace-secret; Path=/"],
        "nested": {"provider_error": "Authorization: Basic auth-secret"},
    }
    public_hash = canonical_payload_hash(payload["public_payload"])

    sanitized = sanitize_diagnostic_fields(payload)

    assert sanitized["public_payload"] == payload["public_payload"]
    assert canonical_payload_hash(sanitized["public_payload"]) == public_hash
    assert "trace-secret" not in repr(sanitized)
    assert "auth-secret" not in repr(sanitized)


def test_dict_repr_headers_nested_tokens_urls_and_all_phone_shapes_are_safe():
    diagnostic = {
        "detail": (
            "{'Authorization': 'Basic dXNlcjpwYXNz', "
            "'Proxy-Authorization': 'Bearer proxy-secret', "
            "'Cookie': 'sid=cookie-secret', "
            "'Set-Cookie': 'session=response-secret'} "
            "call +1 (415) 555-2671 or 010-87654321 / 021 61234567 "
            "id 11010519491231002X"
        ),
        "nested": [
            {"feishu_app_secret": "nested-secret"},
            {"access_token": "nested-access"},
        ],
    }

    rendered = repr(sanitize_tree(diagnostic, redact_pii=True))

    for secret in (
        "dXNlcjpwYXNz",
        "proxy-secret",
        "cookie-secret",
        "response-secret",
        "nested-secret",
        "nested-access",
        "415",
        "87654321",
        "61234567",
        "11010519491231002X",
    ):
        assert secret not in rendered


def test_url_sanitizer_drops_userinfo_sensitive_query_and_fragment_only():
    raw = (
        "https://user:pass@example.com/news?a=1&access_token=url-secret"
        "&page=2#private-fragment"
    )

    assert sanitize_url(raw) == "https://example.com/news?a=1&page=2"


def test_pii_mode_does_not_corrupt_hashes_ids_versions_dates_or_long_numbers():
    payload = {
        "index_content_hash": "e8f767a5536449ca0035794a0f186249a5e859fc2dd",
        "sha256": "9" * 64,
        "event_id": "evt_13800138000_01087654321",
        "draft_id": "tp_629df7cd100c02b2",
        "snapshot_id": "1" * 64,
        "run_id": "run_48a8601f89687959b042373a51fb9478",
        "evidence_hash": "2" * 64,
        "content_hash": "3" * 64,
        "idempotency_hash": "4" * 64,
        "ops_metrics_db": "data/operations-metrics.sqlite",
        "output_dir": "reports-daily/production-output",
        "direction": "commercial_space_infrastructure",
        "prompt_version": "semantic-v27-2026.08",
        "event_date": "2026-08-31",
    }

    assert sanitize_tree(payload, redact_pii=True) == payload


def test_namespaced_sensitive_keys_bytes_and_dict_repr_are_fully_redacted():
    value = {
        "Config.FEISHU_APP_SECRET": "feishu-secret",
        "provider.metaso-api-key": "metaso-secret",
        "MINIMAX_API_KEY": "minimax-secret",
        "aws.secret_access_key": "aws-secret",
        "aws.credentials": {"access": "credential-secret"},
        "token_count": 3,
        "token_hash": "stable-token-hash",
        "prompt.version": "v3",
        "event_date": "2026-09-03",
        "errors": [
            b"{'Authorization': 'Basic basic-secret', "
            b"'Cookie': 'sid=cookie-secret', "
            b"'X-Amz-Security-Token': 'session-secret'}",
            (
                "request.headers: {'X-Api-Key': b'bytes-secret', "
                "'Authorization': b'Bearer byte-bearer'}"
            ),
            "FEISHU_APP_SECRET='multi word secret'",
        ],
    }

    sanitized = sanitize_tree(value, redact_pii=True)
    rendered = repr(sanitized)

    for secret in (
        "feishu-secret",
        "metaso-secret",
        "minimax-secret",
        "aws-secret",
        "credential-secret",
        "basic-secret",
        "cookie-secret",
        "session-secret",
        "bytes-secret",
        "byte-bearer",
        "multi word secret",
    ):
        assert secret not in rendered
    assert sanitized["token_count"] == 3
    assert sanitized["token_hash"] == "stable-token-hash"
    assert sanitized["prompt.version"] == "v3"
    assert sanitized["event_date"] == "2026-09-03"


def test_url_sanitizer_covers_cloud_signatures_and_code_host_tokens():
    raw = (
        "https://user:pass@example.test/object?ordinary=keep&key=secret-key"
        "&api-key=secret-api&X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=secret-credential&X-Amz-Date=20260903T000000Z"
        "&X-Amz-Signature=secret-signature"
        "&X-Amz-Security-Token=secret-session&github_token=secret-github"
        "&gitlab-token=secret-gitlab&page=2#private"
    )

    safe = sanitize_url(raw)

    assert safe == (
        "https://example.test/object?ordinary=keep"
        "&X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Date=20260903T000000Z&page=2"
    )


def test_diagnostic_pii_redaction_requires_no_contact_context():
    diagnostic = (
        "138.0013.8000 / (010) 8765-4321 / 0086 (21) 6123.4567 / "
        "+44 (0)20/7946-0958 / 00 1 (415) 555 2671 / "
        "marcus@example.com / 11010519491231002X"
    )

    safe = sanitize_text(diagnostic, limit=4000)

    for fragment in (
        "138.0013.8000",
        "8765-4321",
        "6123.4567",
        "7946-0958",
        "555 2671",
        "marcus@example.com",
        "11010519491231002X",
    ):
        assert fragment not in safe
    assert "[redacted-phone]" in safe

    for standalone in (
        "010 / 87654321",
        "138 / 0013 / 8000",
        "0086 / 21 / 61234567",
        "+44 / 20 / 79460958",
    ):
        assert sanitize_text(standalone) == "[redacted-phone]"


def test_structured_url_fields_preserve_numeric_article_ids_while_removing_secrets():
    payload = sanitize_tree(
        {
            "canonical_url": (
                "https://www.jiqizhixin.com/articles/2026-08-12-7"
                "?access_token=secret&page=2#fragment"
            ),
            "evidence_urls": [
                "https://example.test/articles/2026-08-12-7?token=secret"
                "&phone=13800138000&email=a%40b.com"
            ],
        },
        redact_pii=True,
    )

    assert payload["canonical_url"] == (
        "https://www.jiqizhixin.com/articles/2026-08-12-7?page=2"
    )
    assert payload["evidence_urls"] == [
        "https://example.test/articles/2026-08-12-7"
        "?phone=%5Bredacted-phone%5D&email=%5Bredacted-email%5D"
    ]

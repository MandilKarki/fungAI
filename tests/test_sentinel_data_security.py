from aegis_core.memory.state_backend import StateBackend
from aegis_sentinel.tools.data_security import (
    classify_data_sensitivity,
    review_access_grants,
    scan_for_exposed_secrets,
)


def test_classify_data_sensitivity_finds_real_pii_and_rejects_bad_luhn():
    sample = (
        "Contact john.doe@corp.com, SSN 123-45-6789, card 4111 1111 1111 1111, "
        "random id 1234567890123"
    )
    result = classify_data_sensitivity(sample)
    assert "us_ssn" in result["findings"]
    assert "email" in result["findings"]
    assert result["findings"]["credit_card"] == ["4111 1111 1111 1111"]
    assert "pii_sensitive" in result["categories"]


def test_classify_data_sensitivity_clean_sample():
    result = classify_data_sensitivity("just some notes about the project architecture")
    assert result["categories"] == ["none_detected"]
    assert result["findings"] == {}


async def test_scan_for_exposed_secrets_finds_real_patterns_only():
    memory = StateBackend()
    await memory.write("/configs/app.env", "AWS_KEY=AKIAABCDEFGHIJKLMNOP\npassword: hunter2_super_secret")
    await memory.write("/notes/readme.txt", "nothing sensitive here")

    hits = await scan_for_exposed_secrets("/", memory)
    kinds = {h["kind"] for h in hits}
    paths = {h["path"] for h in hits}
    assert "aws_access_key" in kinds
    assert "/configs/app.env" in paths
    assert "/notes/readme.txt" not in paths


def test_review_access_grants_flags_broad_and_stale_only():
    grants = [
        {"principal": "svc-deploy", "scope": "s3:*", "last_used_days_ago": 5},
        {"principal": "old-intern", "scope": "repo:read", "last_used_days_ago": 400},
        {"principal": "reader", "scope": "repo:read", "last_used_days_ago": 2},
    ]
    flagged = review_access_grants(grants)
    flagged_principals = {f["principal"] for f in flagged}
    assert flagged_principals == {"svc-deploy", "old-intern"}
    assert "reader" not in flagged_principals

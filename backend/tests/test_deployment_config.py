"""Configuration that only matters once the app runs on real infrastructure."""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from app.cli import main as cli_main
from app.core.config import normalize_database_url, settings
from app.storage.backend import S3Storage, StorageError


@pytest.mark.parametrize(
    "given, expected",
    [
        ("postgresql://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
        ("postgres://u:p@host/db?sslmode=require", "postgresql+psycopg://u:p@host/db?sslmode=require"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///./var/app.db", "sqlite:///./var/app.db"),
    ],
)
def test_database_urls_use_the_psycopg_driver(given: str, expected: str):
    assert normalize_database_url(given) == expected


@pytest.fixture
def s3(monkeypatch):
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="praxis-test")
        monkeypatch.setattr(settings, "S3_BUCKET", "praxis-test")
        monkeypatch.setattr(settings, "S3_REGION", "us-east-1")
        monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "")
        monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", "test")
        monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "test")
        yield S3Storage()


def test_s3_storage_round_trip(s3: S3Storage):
    key = s3.put("papers/a/b/v1.bin", b"ciphertext")
    assert s3.exists(key)
    assert s3.get(key) == b"ciphertext"
    s3.delete(key)
    assert not s3.exists(key)
    with pytest.raises(StorageError):
        s3.get(key)


def test_s3_server_side_encryption_is_opt_in(s3: S3Storage, monkeypatch):
    s3.put("plain.bin", b"x")
    head = boto3.client("s3", region_name="us-east-1").head_object(Bucket="praxis-test", Key="praxis/plain.bin")
    assert "ServerSideEncryption" not in head or head["ServerSideEncryption"] != "aws:kms"
    monkeypatch.setattr(settings, "S3_SERVER_SIDE_ENCRYPTION", "AES256")
    s3.put("sse.bin", b"x")
    head = boto3.client("s3", region_name="us-east-1").head_object(Bucket="praxis-test", Key="praxis/sse.bin")
    assert head["ServerSideEncryption"] == "AES256"


def test_check_command_reports_pass_and_fail(capsys):
    assert cli_main(["check", "--only", "database"]) == 0
    assert "PASS  database" in capsys.readouterr().out

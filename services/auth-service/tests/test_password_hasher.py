import pytest
from auth_app import settings as auth_settings
from auth_app.security import get_password_hasher, hash_password, needs_rehash, verify_password


@pytest.fixture(autouse=True)
def fast_test_hasher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGON2_TIME_COST", "1")
    monkeypatch.setenv("ARGON2_MEMORY_COST", "1024")
    monkeypatch.setenv("ARGON2_PARALLELISM", "1")
    monkeypatch.setenv("ARGON2_HASH_LEN", "32")
    monkeypatch.setenv("ARGON2_SALT_LEN", "16")
    auth_settings.get_settings.cache_clear()

    yield

    auth_settings.get_settings.cache_clear()

@pytest.mark.unit
def test_hash_password_does_not_store_plaintext() -> None:
    password = "super-secret-password"

    hashed = hash_password(password)

    assert hashed != password
    assert hashed.startswith("$argon2id$")

@pytest.mark.unit
def test_hash_password_rejects_empty_password() -> None:
    with pytest.raises(ValueError, match="Password cannot be empty"):
        hash_password("")

@pytest.mark.unit
def test_hash_password_rejects_too_long_password() -> None:
    with pytest.raises(ValueError, match="Password exceeds maximum allowed length"):
        hash_password("x" * 129)

@pytest.mark.unit
def test_verify_password_successfully() -> None:
    password = "super-secret-password"

    hashed = hash_password(password)

    assert verify_password(password, hashed) is True

@pytest.mark.unit
def test_verify_password_rejects_invalid_password() -> None:
    password = "super-secret-password"
    wrong_password = "wrong-password"

    hashed = hash_password(password)

    assert verify_password(wrong_password, hashed) is False

@pytest.mark.unit
def test_verify_password_rejects_invalid_hash() -> None:
    assert verify_password("super-secret-password", "not-a-valid-argon2-hash") is False
    assert needs_rehash("not-a-valid-argon2-hash") is False

@pytest.mark.unit
def test_factory_allows_custom_parameters() -> None:
    custom_hasher = get_password_hasher(
        time_cost=1,
        memory_cost=2048,
        parallelism=1,
        hash_len=32,
        salt_len=16,
    )

    hashed = custom_hasher.hash("custom-password")

    assert hashed.startswith("$argon2id$")
    assert custom_hasher.verify(hashed, "custom-password") is True

@pytest.mark.unit
def test_needs_rehash_detects_outdated_hash() -> None:
    password = "super-secret-password"
    current_hasher = get_password_hasher(
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        salt_len=16,
    )
    legacy_hasher = get_password_hasher(
        time_cost=1,
        memory_cost=1024,
        parallelism=1,
        hash_len=32,
        salt_len=16,
    )

    legacy_hash = legacy_hasher.hash(password)

    assert current_hasher.check_needs_rehash(legacy_hash) is True
    assert needs_rehash(legacy_hash, hasher=current_hasher) is True
    assert verify_password(password, legacy_hash, hasher=current_hasher) is True

@pytest.mark.unit
def test_verify_password_works_with_custom_hasher() -> None:
    password = "custom-password"
    custom_hasher = get_password_hasher(
        time_cost=1,
        memory_cost=2048,
        parallelism=1,
        hash_len=32,
        salt_len=16,
    )

    hashed = hash_password(password, hasher=custom_hasher)

    assert verify_password(password, hashed, hasher=custom_hasher) is True
    assert verify_password("wrong-password", hashed, hasher=custom_hasher) is False

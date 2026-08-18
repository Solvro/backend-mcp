import pytest
from auth_app.security import PasswordManager
from auth_app.settings import AuthSettings


@pytest.fixture
def fast_pm() -> PasswordManager:
    fast_settings = AuthSettings(
        argon2_time_cost=1,
        argon2_memory_cost=1024,
        argon2_parallelism=1,
        argon2_hash_len=32,
        argon2_salt_len=16
    )
    return PasswordManager(settings=fast_settings)


@pytest.mark.unit
def test_hash_password_does_not_store_plaintext(fast_pm: PasswordManager) -> None:
    password = "super-secret-password"

    hashed = fast_pm.hash_password(password)

    assert hashed != password
    assert hashed.startswith("$argon2id$")


@pytest.mark.unit
def test_hash_password_rejects_empty_password(fast_pm: PasswordManager) -> None:
    with pytest.raises(ValueError, match="Password cannot be empty"):
        fast_pm.hash_password("")


@pytest.mark.unit
def test_hash_password_rejects_too_long_password(fast_pm: PasswordManager) -> None:
    with pytest.raises(ValueError, match="Password exceeds maximum allowed length"):
        fast_pm.hash_password("x" * (fast_pm._settings.max_password_length + 1))


@pytest.mark.unit
def test_verify_password_successfully(fast_pm: PasswordManager) -> None:
    password = "super-secret-password"

    hashed = fast_pm.hash_password(password)

    assert fast_pm.verify_password(password, hashed) is True


@pytest.mark.unit
def test_verify_password_rejects_invalid_password(fast_pm: PasswordManager) -> None:
    password = "super-secret-password"
    wrong_password = "wrong-password"

    hashed = fast_pm.hash_password(password)

    assert fast_pm.verify_password(wrong_password, hashed) is False


@pytest.mark.unit
def test_verify_password_rejects_invalid_hash(fast_pm: PasswordManager) -> None:
    assert fast_pm.verify_password("super-secret-password", "not-a-valid-argon2-hash") is False
    assert fast_pm.needs_rehash("not-a-valid-argon2-hash") is False


@pytest.mark.unit
def test_needs_rehash_detects_outdated_hash() -> None:
    legacy_settings = AuthSettings(
        argon2_time_cost=1, argon2_memory_cost=1024, argon2_parallelism=1,
        argon2_hash_len=32, argon2_salt_len=16
    )
    current_settings = AuthSettings(
        argon2_time_cost=3, argon2_memory_cost=65536, argon2_parallelism=4,
        argon2_hash_len=32, argon2_salt_len=16
    )

    legacy_pm = PasswordManager(settings=legacy_settings)
    current_pm = PasswordManager(settings=current_settings)

    password = "super-secret-password"
    legacy_hash = legacy_pm.hash_password(password)

    assert current_pm.needs_rehash(legacy_hash) is True
    assert current_pm.verify_password(password, legacy_hash) is True

@pytest.mark.unit
def test_none_hash_needs_rehash(fast_pm: PasswordManager) -> None:
    assert fast_pm.needs_rehash(None) is False

@pytest.mark.unit
def test_non_string_hash_needs_rehash(fast_pm: PasswordManager) -> None:
    assert fast_pm.needs_rehash(12345) is False

@pytest.mark.unit
def test_none_password_verification(fast_pm: PasswordManager) -> None:
    assert fast_pm.verify_password(None, "somehash") is False

@pytest.mark.unit
def test_none_hash_password_verification(fast_pm: PasswordManager) -> None:
    assert fast_pm.verify_password("password", None) is False

@pytest.mark.unit
def test_non_string_password_verification(fast_pm: PasswordManager) -> None:
    assert fast_pm.verify_password("password", 12345) is False

@pytest.mark.unit
def test_non_string_hash_password_verification(fast_pm: PasswordManager) -> None:
    assert fast_pm.verify_password("password", object()) is False

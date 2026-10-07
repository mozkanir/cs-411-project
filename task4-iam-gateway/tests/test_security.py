from ollama_iam_gateway.iam import security


def test_hash_is_argon2id_and_salted():
    h1 = security.hash_password("correct horse")
    h2 = security.hash_password("correct horse")
    assert h1.startswith("$argon2id$")
    assert "correct horse" not in h1
    assert h1 != h2  # random salt


def test_verify():
    h = security.hash_password("s3cret-pass")
    assert security.verify_password(h, "s3cret-pass") is True
    assert security.verify_password(h, "wrong") is False


def test_verify_never_raises_on_garbage_hash():
    assert security.verify_password("not-a-hash", "x") is False
    assert security.password_needs_rehash(security.hash_password("abcdefgh")) is False

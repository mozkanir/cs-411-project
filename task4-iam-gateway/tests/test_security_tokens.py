from ollama_iam_gateway.iam import security


def test_tokens_are_random_and_long():
    a, b = security.generate_token(), security.generate_token()
    assert a != b and len(a) >= 43


def test_hash_token_is_sha256_hex():
    h = security.hash_token("abc")
    assert len(h) == 64 and h != "abc"
    assert h == security.hash_token("abc")


def test_api_key_format_and_parse():
    raw, prefix = security.generate_api_key()
    assert raw.startswith(f"olg_{prefix}_")
    assert security.parse_api_key_prefix(raw) == prefix


def test_parse_rejects_garbage():
    for bad in ["", "abc", "olg_short_x", "sk_12345678_" + "a" * 40,
                "olg_ZZZZZZZZ_" + "a" * 40, "olg_12345678_tooshort"]:
        assert security.parse_api_key_prefix(bad) is None

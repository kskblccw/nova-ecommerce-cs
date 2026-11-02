"""密码、手机号与 JWT 的纯函数测试。"""

from ecommerce_cs.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_phone,
    hash_token,
    mask_phone,
    verify_password,
)


def test_password_roundtrip() -> None:
    hashed = hash_password("shop123456")
    assert hashed != "shop123456"
    assert verify_password("shop123456", hashed)
    assert not verify_password("wrong-password", hashed)


def test_phone_hash_and_mask() -> None:
    first = hash_phone("13800001001")
    second = hash_phone("13800001001")
    assert first == second
    assert first != hash_phone("13800001002")
    assert mask_phone("13800001001") == "138****1001"


def test_token_roundtrip() -> None:
    token = "session-token-value"
    assert hash_token(token) != token

    jwt_token = create_access_token(subject="1", session_id="abc")
    payload = decode_access_token(jwt_token)
    assert payload is not None
    assert payload["sub"] == "1"
    assert payload["sid"] == "abc"
    assert decode_access_token("invalid-token") is None

from skland.access import CredentialCommandAccessPolicy


def test_private_chat_is_always_allowed():
    policy = CredentialCommandAccessPolicy.from_values([])
    assert policy.allows("")


def test_group_requires_id_or_unified_origin_match():
    policy = CredentialCommandAccessPolicy.from_values(
        ["123456", "aiocqhttp:GroupMessage:654321", "  "]
    )

    assert policy.allows("123456")
    assert policy.allows("654321", "aiocqhttp:GroupMessage:654321")
    assert not policy.allows("999999", "aiocqhttp:GroupMessage:999999")

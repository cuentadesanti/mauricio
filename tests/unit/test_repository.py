from backend.db.repository import hash_messages


def test_hash_is_stable():
    a = [{"role": "user", "content": "hola"}]
    b = [{"role": "user", "content": "hola"}]
    assert hash_messages(a) == hash_messages(b)


def test_hash_changes_with_content():
    a = [{"role": "user", "content": "hola"}]
    b = [{"role": "user", "content": "adiós"}]
    assert hash_messages(a) != hash_messages(b)

from __future__ import annotations

import hashlib
import hmac

from github_webhook import verify_webhook_signature


def test_webhook_hmac_validation():
    payload = b'{"action":"opened"}'
    signature = "sha256=" + hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(payload, signature, "secret")
    assert not verify_webhook_signature(payload + b"x", signature, "secret")
    assert not verify_webhook_signature(payload, None, "secret")

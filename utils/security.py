import hmac
import hashlib

def generate_session_signature(session_id: str, secret: str) -> str:
    """
    Generates a secure HMAC-SHA256 signature for the given session_id using the secret key.
    """
    return hmac.new(
        secret.encode("utf-8"),
        session_id.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def verify_session_signature(session_id: str, signature: str, secret: str) -> bool:
    """
    Verifies if the given signature is valid for the session_id using the secret key.
    """
    expected = generate_session_signature(session_id, secret)
    return hmac.compare_digest(expected, signature)

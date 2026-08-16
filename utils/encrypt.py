import base64
import hashlib
from cryptography.fernet import Fernet
from config import settings

def _get_fernet_key() -> bytes:
    """Derive a valid url-safe base64 32-byte Fernet key from the BOT_TOKEN or FERNET_SECRET_KEY"""
    if hasattr(settings, "FERNET_SECRET_KEY") and settings.FERNET_SECRET_KEY:
        secret_source = settings.FERNET_SECRET_KEY
    else:
        secret_source = settings.BOT_TOKEN
        
    # Hash the secret source to get exactly 32 bytes
    token_hash = hashlib.sha256(secret_source.encode()).digest()
    # Base64 encode it as Fernet requires url-safe base64 key
    return base64.urlsafe_b64encode(token_hash)

def encrypt_key(plain_key: str) -> str:
    """Encrypts a plain API key string using Fernet AES."""
    try:
        key = _get_fernet_key()
        f = Fernet(key)
        encrypted_bytes = f.encrypt(plain_key.encode())
        return encrypted_bytes.decode()
    except Exception as e:
        raise ValueError(f"Sirlashda xatolik yuz berdi: {e}")

def decrypt_key(encrypted_key: str) -> str:
    """Decrypts an encrypted API key back to plain text."""
    try:
        key = _get_fernet_key()
        f = Fernet(key)
        decrypted_bytes = f.decrypt(encrypted_key.encode())
        return decrypted_bytes.decode()
    except Exception as e:
        raise ValueError(f"Shifrni ochishda xatolik yuz berdi: {e}")

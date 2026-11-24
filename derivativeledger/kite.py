"""Kite Connect OAuth helpers. Secrets and access tokens stay server-side."""
from cryptography.fernet import Fernet, InvalidToken
from kiteconnect import KiteConnect
from flask import current_app


class KiteConfigurationError(ValueError):
    pass


def configured_client():
    api_key = current_app.config.get("KITE_API_KEY")
    if not api_key:
        raise KiteConfigurationError("KITE_API_KEY is not configured.")
    return KiteConnect(api_key=api_key)


def api_secret():
    secret = current_app.config.get("KITE_API_SECRET")
    if not secret:
        raise KiteConfigurationError("KITE_API_SECRET is not configured.")
    return secret


def token_cipher():
    key = current_app.config.get("KITE_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise KiteConfigurationError("KITE_TOKEN_ENCRYPTION_KEY is not configured.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise KiteConfigurationError("KITE_TOKEN_ENCRYPTION_KEY is invalid; generate a Fernet key.") from exc


def encrypt_access_token(access_token):
    return token_cipher().encrypt(access_token.encode("utf-8"))


def decrypt_access_token(ciphertext):
    try:
        return token_cipher().decrypt(bytes(ciphertext)).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise KiteConfigurationError("Stored Kite token could not be decrypted. Check the configured encryption key.") from exc

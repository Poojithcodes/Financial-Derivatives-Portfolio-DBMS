import hashlib
import json
import unittest
import time
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from derivativeledger import create_app
from derivativeledger.services import money, verify_audit_chain, execute_trade
from derivativeledger.kite import encrypt_access_token, decrypt_access_token


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents

    def find(self):
        return self

    def sort(self, _key, _direction):
        return iter(sorted(self.documents, key=lambda item: item["sequence"]))


class FakeDatabase:
    def __init__(self, documents):
        self.documents = documents

    def __getitem__(self, _name):
        return FakeCollection(self.documents)


class ServiceTests(unittest.TestCase):
    def test_money_uses_two_decimal_places(self):
        self.assertEqual(money("12.345"), money("12.35"))

    def test_hash_chain_verifies_and_detects_tampering(self):
        payload = {"settlement_id": 3, "value": "12.50"}
        previous = "0" * 64
        canonical = json.dumps({"sequence": 1, "previous_hash": previous, "payload": payload}, sort_keys=True, separators=(",", ":"))
        doc = {"sequence": 1, "previous_hash": previous, "payload": payload, "hash": hashlib.sha256(canonical.encode()).hexdigest()}
        with patch("derivativeledger.services.mongo_database", return_value=FakeDatabase([doc])):
            self.assertEqual(verify_audit_chain(), {"valid": True, "checked": 1})
        changed = dict(doc, payload={"settlement_id": 3, "value": "99.00"})
        with patch("derivativeledger.services.mongo_database", return_value=FakeDatabase([changed])):
            result = verify_audit_chain()
        self.assertFalse(result["valid"])
        self.assertEqual(result["failed_sequence"], 1)

    def test_invalid_trade_is_rejected_before_database_access(self):
        with patch("derivativeledger.services.mysql_connection", side_effect=AssertionError("database must not be called")):
            with self.assertRaises(ValueError):
                execute_trade(1, 1, "BUY", 0, 2)


class ApplicationTests(unittest.TestCase):
    def test_registration_page_and_templates_load(self):
        class TestConfig:
            SECRET_KEY = "test-key"
            TESTING = True
            DEBUG = False
        app = create_app(TestConfig)
        for name in app.jinja_env.list_templates():
            app.jinja_env.get_template(name)
        response = app.test_client().get("/register")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Create trader account", response.data)
        self.assertIn(b"csrf_token", response.data)

    def test_kite_redirect_builds_stateful_developer_login_url(self):
        class TestConfig:
            SECRET_KEY = "test-key"
            TESTING = True
            DEBUG = False
            KITE_API_KEY = "test-api-key"
            KITE_API_SECRET = "test-api-secret"
            KITE_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
            KITE_REDIRECT_URL = "http://localhost/kite/callback"
        app = create_app(TestConfig)
        fake_kite = Mock()
        fake_kite.login_url.return_value = "https://kite.zerodha.com/connect/login?api_key=test-api-key&v=3"
        with patch("derivativeledger.web.configured_client", return_value=fake_kite):
            client = app.test_client()
            with client.session_transaction() as state:
                state.update(user_id=9, user_name="Trader", role="TRADER")
            response = client.get("/kite/connect")
        self.assertEqual(response.status_code, 302)
        location = urlsplit(response.headers["Location"])
        params = parse_qs(location.query)
        returned_state = parse_qs(params["redirect_params"][0])["state"][0]
        self.assertTrue(returned_state)
        self.assertEqual(location.netloc, "kite.zerodha.com")

    def test_kite_page_explains_missing_configuration_and_keeps_cta_visible(self):
        class TestConfig:
            SECRET_KEY = "test-key"
            TESTING = True
            DEBUG = False
            KITE_API_KEY = ""
            KITE_API_SECRET = ""
            KITE_TOKEN_ENCRYPTION_KEY = ""
            KITE_REDIRECT_URL = "http://127.0.0.1:5000/kite/callback"
        app = create_app(TestConfig)
        with patch("derivativeledger.web.query", return_value=None):
            client = app.test_client()
            with client.session_transaction() as state:
                state.update(user_id=9, user_name="Trader", role="TRADER")
            response = client.get("/kite")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Connect to Kite", response.data)
        self.assertIn(b"KITE_API_KEY, KITE_API_SECRET, KITE_TOKEN_ENCRYPTION_KEY", response.data)

    def test_kite_callback_exchanges_token_and_stores_only_ciphertext(self):
        key = Fernet.generate_key().decode()
        class TestConfig:
            SECRET_KEY = "test-key"
            TESTING = True
            DEBUG = False
            KITE_API_KEY = "test-api-key"
            KITE_API_SECRET = "test-api-secret"
            KITE_TOKEN_ENCRYPTION_KEY = key
            KITE_REDIRECT_URL = "http://localhost/kite/callback"
        app = create_app(TestConfig)
        fake_kite = Mock()
        fake_kite.generate_session.return_value = {"access_token": "secret-access-token", "user_id": "AB1234", "user_name": "Test Kite User"}
        state_value = "oauth-state"
        with patch("derivativeledger.web.configured_client", return_value=fake_kite), patch("derivativeledger.web.execute") as execute:
            client = app.test_client()
            with client.session_transaction() as state:
                state.update(user_id=9, user_name="Trader", role="TRADER", kite_oauth_state=state_value, kite_oauth_started=int(time.time()))
            response = client.get(f"/kite/callback?status=success&request_token=one-time&state={state_value}")
        self.assertEqual(response.status_code, 302)
        fake_kite.generate_session.assert_called_once_with("one-time", api_secret="test-api-secret")
        saved_ciphertext = execute.call_args.args[1][3]
        self.assertNotIn(b"secret-access-token", saved_ciphertext)
        with app.app_context():
            self.assertEqual(decrypt_access_token(saved_ciphertext), "secret-access-token")

    def test_encrypted_kite_token_round_trip(self):
        class TestConfig:
            SECRET_KEY = "test-key"
            KITE_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
        app = create_app(TestConfig)
        with app.app_context():
            ciphertext = encrypt_access_token("token-value")
            self.assertEqual(decrypt_access_token(ciphertext), "token-value")


if __name__ == "__main__":
    unittest.main()

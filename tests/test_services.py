import hashlib
import json
import unittest
from unittest.mock import patch

from derivativeledger import create_app
from derivativeledger.services import money, verify_audit_chain, execute_trade


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


if __name__ == "__main__":
    unittest.main()

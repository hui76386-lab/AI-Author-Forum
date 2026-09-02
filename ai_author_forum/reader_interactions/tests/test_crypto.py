import hashlib

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from ..crypto import EmailProtector, email_lookup_digest, normalize_email


class ReaderCryptoTests(SimpleTestCase):
    def test_email_normalization_preserves_local_semantics_and_normalizes_domain(self):
        self.assertEqual(
            normalize_email("  Reader.Name+tag@BÜCHER.example  "),
            "Reader.Name+tag@xn--bcher-kva.example",
        )
        self.assertNotEqual(
            normalize_email("Reader.Name+tag@example.org"),
            normalize_email("readername@example.org"),
        )

    def test_invalid_email_is_rejected(self):
        for value in (
            "",
            "reader",
            "reader@@example.org",
            "reader@",
            "reader@example.org.",
        ):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalize_email(value)

    @override_settings(READER_EMAIL_LOOKUP_KEY="lookup-key-" * 8)
    def test_email_lookup_is_keyed_and_not_plain_sha256(self):
        normalized = "Reader@example.org"
        self.assertNotEqual(
            email_lookup_digest(normalized),
            hashlib.sha256(normalized.encode()).hexdigest(),
        )

    def test_versioned_multifernet_reads_old_values_and_writes_with_first_key(self):
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        old = EmailProtector(f"1:{old_key}")
        old_value = old.encrypt_text("Reader@example.org")

        rotated = EmailProtector(f"2:{new_key},1:{old_key}")
        self.assertEqual(
            rotated.decrypt_text(old_value.ciphertext), "Reader@example.org"
        )
        new_value = rotated.encrypt_text("Reader@example.org")
        self.assertEqual(new_value.key_version, 2)
        self.assertEqual(
            rotated.decrypt_text(new_value.ciphertext), "Reader@example.org"
        )

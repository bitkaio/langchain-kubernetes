"""Unit tests for label value sanitization."""

from __future__ import annotations

from langchain_kubernetes._labels import sanitize_label_value


# ---------------------------------------------------------------------------
# sanitize_label_value
# ---------------------------------------------------------------------------


class TestSanitizeLabelValue:
    def test_valid_value_unchanged(self):
        safe, orig = sanitize_label_value("my-thread-123")
        assert safe == "my-thread-123"
        assert orig is None

    def test_value_with_invalid_chars_is_hashed(self):
        safe, orig = sanitize_label_value("thread@user/conv#1")
        assert len(safe) == 12
        assert safe == safe.lower()  # hex
        assert orig == "thread@user/conv#1"

    def test_value_too_long_is_hashed(self):
        long_val = "a" * 64
        safe, orig = sanitize_label_value(long_val)
        assert len(safe) == 12
        assert orig == long_val

    def test_exactly_63_chars_valid(self):
        val = "a" * 63
        safe, orig = sanitize_label_value(val)
        assert safe == val
        assert orig is None

    def test_single_char_valid(self):
        safe, orig = sanitize_label_value("a")
        assert safe == "a"
        assert orig is None

    def test_empty_string_valid(self):
        safe, orig = sanitize_label_value("")
        assert safe == ""
        assert orig is None

    def test_hash_is_stable(self):
        val = "unstable-value!@#"
        safe1, _ = sanitize_label_value(val)
        safe2, _ = sanitize_label_value(val)
        assert safe1 == safe2

    def test_different_inputs_produce_different_hashes(self):
        safe1, _ = sanitize_label_value("a" * 64)
        safe2, _ = sanitize_label_value("b" * 64)
        assert safe1 != safe2

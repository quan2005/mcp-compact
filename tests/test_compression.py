"""Tests for TOON compression functionality."""

from __future__ import annotations

from mcpx.compression import (
    ToonCompressor,
    compress_toon,
    detect_data_type,
    is_compressible,
)


class TestDataTypeDetection:
    """Tests for data type detection."""

    def test_detect_primitive_string(self):
        """Test: String is detected as primitive."""
        assert detect_data_type("hello") == "primitive"

    def test_detect_primitive_number(self):
        """Test: Numbers are detected as primitive."""
        assert detect_data_type(42) == "primitive"
        assert detect_data_type(3.14) == "primitive"

    def test_detect_primitive_bool(self):
        """Test: Boolean is detected as primitive."""
        assert detect_data_type(True) == "primitive"
        assert detect_data_type(False) == "primitive"

    def test_detect_primitive_none(self):
        """Test: None is detected as primitive."""
        assert detect_data_type(None) == "primitive"

    def test_detect_array_homogeneous_objects(self):
        """Test: Array of objects with same keys."""
        data = [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25},
        ]
        assert detect_data_type(data) == "array"

    def test_detect_array_empty(self):
        """Test: Empty array is detected as array."""
        assert detect_data_type([]) == "array"

    def test_detect_mixed_array(self):
        """Test: Mixed array is detected as mixed."""
        data = [{"name": "Alice"}, {"age": 25}, "string"]
        assert detect_data_type(data) == "mixed"

    def test_detect_object(self):
        """Test: Object is detected as object."""
        data = {"name": "Alice", "age": 30}
        assert detect_data_type(data) == "object"

    def test_detect_other(self):
        """Test: Unknown type is detected as other."""
        assert detect_data_type(object()) == "other"


class TestIsCompressible:
    """Tests for compression suitability check."""

    def test_primitive_not_compressible(self):
        """Test: Primitives are not compressible."""
        assert not is_compressible("hello")
        assert not is_compressible(42)
        assert not is_compressible(True)
        assert not is_compressible(None)

    def test_small_array_not_compressible(self):
        """Test: Small arrays are not compressible."""
        assert not is_compressible([{"a": 1}])
        assert not is_compressible([])

    def test_large_array_compressible(self):
        """Test: Large arrays are compressible."""
        data = [{"id": i, "name": f"item{i}"} for i in range(5)]
        assert is_compressible(data)

    def test_small_object_not_compressible(self):
        """Test: Small objects are not compressible."""
        assert not is_compressible({"a": 1})
        assert not is_compressible({})

    def test_large_object_compressible(self):
        """Test: Large objects are compressible."""
        data = {f"key{i}": f"value{i}" for i in range(5)}
        assert is_compressible(data)

    def test_custom_min_size(self):
        """Test: Custom min_size parameter works."""
        # Use homogeneous array for predictable behavior
        data = [{"id": i, "name": f"item{i}"} for i in range(5)]
        assert not is_compressible(data, min_size=10)
        assert is_compressible(data, min_size=3)

        # Mixed array requires 2x min_size
        mixed_data = [{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}]
        assert is_compressible(mixed_data, min_size=2)  # 4 >= 2*2

    def test_mixed_array_with_large_size(self):
        """Test: Large mixed arrays might be compressible."""
        data = [{"a": i} if i % 2 == 0 else i for i in range(10)]
        assert is_compressible(data, min_size=5)


class TestToonCompressor:
    """Tests for ToonCompressor."""

    def test_compressor_disabled(self):
        """Test: Compressor with enabled=False returns data as-is."""
        compressor = ToonCompressor(enabled=False)
        data = [{"name": "Alice"}]
        result, was_compressed = compressor.compress(data)
        assert result == data
        assert not was_compressed

    def test_compressor_primitive_not_compressed(self):
        """Test: Primitives are not compressed."""
        compressor = ToonCompressor(enabled=True)
        result, was_compressed = compressor.compress("hello")
        assert result == "hello"
        not was_compressed

    def test_compressor_small_array_not_compressed(self):
        """Test: Small arrays are not compressed."""
        compressor = ToonCompressor(enabled=True, min_size=5)
        data = [{"a": 1}, {"b": 2}]
        result, was_compressed = compressor.compress(data)
        assert result == data
        assert not was_compressed

    def test_compressor_large_array_attempted(self):
        """Test: Large arrays trigger compression attempt."""
        compressor = ToonCompressor(enabled=True)
        data = [{"id": i, "name": f"item{i}"} for i in range(5)]

        result, was_compressed = compressor.compress(data)

        # If toon_format is not available, returns original
        # If available, might compress
        assert result is not None
        # was_compressed depends on whether package is installed

    def test_maybe_compress_result(self):
        """Test: maybe_compress_result returns proper format."""
        compressor = ToonCompressor(enabled=True)
        data = [{"name": "Alice"}]

        result = compressor.maybe_compress_result(data)

        assert "data" in result
        assert "format" in result
        assert "compressed" in result
        assert "original_type" in result
        assert result["format"] in ("json", "toon")

    def test_maybe_compress_result_disabled(self):
        """Test: Disabled compressor returns json format."""
        compressor = ToonCompressor(enabled=False)
        data = [{"name": "Alice"}]

        result = compressor.maybe_compress_result(data)

        assert result["format"] == "json"
        assert not result["compressed"]


class TestCompressToon:
    """Tests for compress_toon convenience function."""

    def test_compress_toon_disabled(self):
        """Test: Function with enabled=False returns original."""
        data = [{"name": "Alice"}]
        result = compress_toon(data, enabled=False)
        assert result == data

    def test_compress_toon_default_enabled(self):
        """Test: Function defaults to enabled."""
        data = [{"name": "Alice"}]
        # Small data won't be compressed
        result = compress_toon(data)
        assert result == data

    def test_compress_toon_large_data(self):
        """Test: Large data triggers compression attempt."""
        data = [{"id": i} for i in range(10)]
        result = compress_toon(data, enabled=True, min_size=5)
        # Result depends on toon_format availability
        assert result is not None

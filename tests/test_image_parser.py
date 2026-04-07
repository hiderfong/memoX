"""图片 OCR 解析器测试"""
import sys, os, pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from knowledge.document_parser import ImageParser, DocumentParser


def _create_test_image(path: Path) -> Path:
    """创建一个简单的测试 PNG 图片"""
    import base64
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
    )
    path.write_bytes(png_data)
    return path


@pytest.mark.asyncio
async def test_image_parser_qwen_vl_success():
    """Qwen VL 主路径成功返回 OCR 文本"""
    parser = ImageParser(dashscope_api_key="fake-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "图片中的文字：Hello World"}}]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            _create_test_image(Path(f.name))
            doc = await parser.parse(Path(f.name), "img_001")
            os.unlink(f.name)

    assert "Hello World" in doc.content
    assert doc.metadata["type"] == "image"
    assert doc.metadata["ocr_method"] == "qwen-vl"


@pytest.mark.asyncio
async def test_image_parser_fallback_to_pytesseract():
    """Qwen VL 失败时回退到 pytesseract"""
    parser = ImageParser(dashscope_api_key="fake-key")

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Network error")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        with patch.object(parser, "_ocr_pytesseract", return_value="Fallback text"):
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                _create_test_image(Path(f.name))
                doc = await parser.parse(Path(f.name), "img_002")
                os.unlink(f.name)

    assert "Fallback text" in doc.content
    assert doc.metadata["ocr_method"] == "pytesseract"


@pytest.mark.asyncio
async def test_image_parser_no_api_key_uses_pytesseract():
    """无 API key 时直接用 pytesseract"""
    parser = ImageParser(dashscope_api_key="")

    with patch.object(parser, "_ocr_pytesseract", return_value="Local OCR result"):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            _create_test_image(Path(f.name))
            doc = await parser.parse(Path(f.name), "img_003")
            os.unlink(f.name)

    assert "Local OCR result" in doc.content


def test_document_parser_registers_image_types():
    """DocumentParser 注册了图片格式"""
    dp = DocumentParser()
    for ext in [".png", ".jpg", ".jpeg", ".webp"]:
        parser = dp.get_parser(f"test{ext}")
        assert isinstance(parser, ImageParser)

"""Tests for message utility functions."""

from kosong.base.message import ImageURLPart, Message, TextPart

from kimi_cli.utils.message import message_extract_text


def test_extract_text_from_string_content():
    """Test extracting text from message with string content."""
    message = Message(role="user", content="Simple text")
    result = message_extract_text(message)

    assert result == "Simple text"


def test_extract_text_from_content_parts():
    """Test extracting text from message with content parts."""
    text_part1 = TextPart(text="Hello")
    text_part2 = TextPart(text="World")
    image_part = ImageURLPart(image_url=ImageURLPart.ImageURL(url="https://example.com/image.jpg"))

    message = Message(role="user", content=[text_part1, image_part, text_part2])
    result = message_extract_text(message)

    assert result == "Hello\nWorld"


def test_extract_text_from_empty_content_parts():
    """Test extracting text from message with no text parts."""
    image_part = ImageURLPart(image_url=ImageURLPart.ImageURL(url="https://example.com/image.jpg"))
    message = Message(role="user", content=[image_part])
    result = message_extract_text(message)

    assert result == ""


def test_extract_text_from_empty_string():
    """Test extracting text from empty string content."""
    message = Message(role="user", content="")
    result = message_extract_text(message)

    assert result == ""

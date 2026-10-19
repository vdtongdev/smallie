from io import BytesIO

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile

from app.core.config import get_settings
from app.features.documents.routes import upload


def rejected(filename: str) -> HTTPException:
    """The error upload() raises for this file; validation runs before anything is written."""
    with pytest.raises(HTTPException) as error:
        upload([UploadFile(BytesIO(b"x"), filename=filename)], BackgroundTasks(), request=None, user_id="u1")
    return error.value


def test_images_are_rejected_when_image_reading_is_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_vision_model", "")
    error = rejected("receipt.PNG")
    assert error.status_code == 400 and error.detail == "receipt.PNG: image reading is turned off"


def test_other_types_name_the_supported_ones(monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_vision_model", "vision-a")
    error = rejected("notes.txt")
    assert error.status_code == 400
    assert error.detail == "notes.txt: only PDF, DOCX or image (PNG, JPG, WEBP) files are supported"

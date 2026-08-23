import pytest
from pydantic import BaseModel, ValidationError

from bgconvertor.llm.client import _lenient_validate


class Reading(BaseModel):
    code: str
    value: float


def test_direct_json():
    assert _lenient_validate(Reading, '{"code": "51.02", "value": 1.5}').value == 1.5


def test_markdown_fenced():
    text = 'Iată transcrierea:\n```json\n{"code": "51.02", "value": 2}\n```\nGata.'
    assert _lenient_validate(Reading, text).code == "51.02"


def test_prose_wrapped():
    text = 'Rezultatul este {"code": "65.02", "value": 3.25} conform paginii.'
    assert _lenient_validate(Reading, text).value == 3.25


def test_hopeless_still_raises():
    with pytest.raises(ValidationError):
        _lenient_validate(Reading, "nu pot citi pagina")

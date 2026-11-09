from types import SimpleNamespace
import pytest
from app.core.llm_client import LLMClient, LLMError
from app.service import parse_service
from app.schemas.parse import ResumeSchema


def test_invalid_json_and_schema_responses_are_retained_in_order():
    outputs=iter(['not-json', '[]'])
    def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(outputs)))])
    client=LLMClient(client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    events=[]
    with pytest.raises(LLMError):
        client.chat_json('合成输入',ResumeSchema,prompt_name='parse',on_result=events.append)
    assert [event['attempt'] for event in events]==[1,2]
    assert [event['raw_text'] for event in events]==['not-json','[]']
    assert all(event['status']=='failed' and event['error'] for event in events)


def test_semantic_failures_keep_both_actual_outputs_and_reset_the_sink(monkeypatch):
    output=ResumeSchema.model_validate({'basic':{'name':'合成无经历'}})
    monkeypatch.setattr(parse_service.llm_client,'chat_json',lambda *args,**kwargs:output)
    events=[]
    with parse_service.capture_calls(events.append):
        with pytest.raises(parse_service.ParseError):
            parse_service.extract_structured('只有姓名，没有经历')
    structured=[event for event in events if event['kind']=='structured']
    assert len(structured)==2
    assert all(event['output']['employments']==[] for event in structured)
    before=len(events)
    with pytest.raises(parse_service.ParseError):
        parse_service.extract_structured('只有姓名，没有经历')
    assert len(events)==before

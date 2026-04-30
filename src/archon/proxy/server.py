"""FastAPI server that fakes the Anthropic API in front of LiteLLM.

Adapted from the upstream anthropic-proxy script. Differences:

- No Vertex AI support — Gemini goes straight through the regular
  Gemini API (``GEMINI_API_KEY``). Drops ``google-auth`` and
  ``google-cloud-aiplatform`` from the dep set.
- Default model lists refreshed (April 2026). Defaults can be
  overridden via env vars (BIG_MODEL / SMALL_MODEL / OPENAI_BASE_URL
  / PREFERRED_PROVIDER).
- Reads port from ``ARCHON_PROXY_PORT`` so multiple lanes can run
  concurrent proxies on different ports.
- Maps Claude's ``opus`` model name (Archon's default) to BIG_MODEL,
  in addition to the upstream ``haiku``→SMALL / ``sonnet``→BIG mapping.

Heavy deps (fastapi, uvicorn, litellm, pydantic) are required only
when this server runs — the rest of Archon doesn't import them.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Literal, Optional, Union

import httpx  # noqa: F401  (kept for users who import this module directly)
import litellm
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator


# ── logging setup ─────────────────────────────────────────────────────


logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

logging.getLogger('uvicorn').setLevel(logging.WARNING)
logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
logging.getLogger('uvicorn.error').setLevel(logging.WARNING)


class _MessageFilter(logging.Filter):
    """Drop the noisier LiteLLM / cost-calc internals; we don't need them."""
    _BLOCK = (
        'LiteLLM completion()',
        'HTTP Request:',
        'selected model name for cost calculation',
        'utils.py',
        'cost_calculator',
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = getattr(record, 'msg', '')
        if isinstance(msg, str):
            for marker in self._BLOCK:
                if marker in msg:
                    return False
        return True


logging.getLogger().addFilter(_MessageFilter())


# ── env / model config ────────────────────────────────────────────────


ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
OPENAI_BASE_URL = os.environ.get('OPENAI_BASE_URL')
PREFERRED_PROVIDER = os.environ.get('PREFERRED_PROVIDER', 'openai').lower()

# Default model picks per provider. The lane wrapper passes BIG_MODEL /
# SMALL_MODEL via env when it spawns this proxy — our fallbacks are
# sensible flagships from each family as of 2026-04. Override at run
# time via env vars; you do not need to touch this file.
BIG_MODEL = os.environ.get('BIG_MODEL', 'gpt-5.4')
SMALL_MODEL = os.environ.get('SMALL_MODEL', 'gpt-5.4-mini')

# OpenAI flagship + reasoning families currently exposed via the API.
OPENAI_MODELS = [
    # 5.5 line
    'gpt-5.5', 'gpt-5.5-pro',
    # 5.4 line
    'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.4-nano', 'gpt-5.4-pro',
    # 5.2 / 5.1 / 5
    'gpt-5.2', 'gpt-5.2-pro',
    'gpt-5.1',
    'gpt-5', 'gpt-5-mini', 'gpt-5-nano', 'gpt-5-pro',
    # 4.1 line (still supported)
    'gpt-4.1', 'gpt-4.1-mini', 'gpt-4.1-nano',
    # 4o
    'gpt-4o', 'gpt-4o-mini',
    # o-series reasoning models
    'o4-mini',
    'o3', 'o3-mini', 'o3-pro',
    'o1', 'o1-mini', 'o1-pro',
]

# Gemini 2.5 family — the regular Gemini API (no Vertex AI).
GEMINI_MODELS = [
    'gemini-2.5-flash',
    'gemini-2.5-flash-lite',
    'gemini-2.5-pro',
]


# ── helpers ───────────────────────────────────────────────────────────


def clean_gemini_schema(schema: Any) -> Any:
    """Recursively strip JSON-schema fields Gemini's tool-args parser doesn't accept."""
    if isinstance(schema, dict):
        schema.pop('additionalProperties', None)
        schema.pop('default', None)
        if schema.get('type') == 'string' and 'format' in schema:
            allowed = {'enum', 'date-time'}
            if schema['format'] not in allowed:
                schema.pop('format')
        for key, value in list(schema.items()):
            schema[key] = clean_gemini_schema(value)
        return schema
    if isinstance(schema, list):
        return [clean_gemini_schema(x) for x in schema]
    return schema


def _strip_provider_prefix(v: str) -> str:
    if v.startswith('anthropic/'):
        return v[len('anthropic/'):]
    if v.startswith('openai/'):
        return v[len('openai/'):]
    if v.startswith('gemini/'):
        return v[len('gemini/'):]
    return v


def _map_model_name(v: str) -> tuple[str, bool]:
    """Map a Claude-shaped model name to the LiteLLM-prefixed target.

    Returns (new_model, mapped). Rules:
      - PREFERRED_PROVIDER == 'anthropic': just add the prefix, no remap.
      - 'haiku' in name → SMALL_MODEL on preferred provider.
      - 'sonnet'/'opus' in name → BIG_MODEL on preferred provider.
      - bare model that matches a known list → add the right prefix.
      - else: leave as-is (caller logs a warning).
    """
    clean = _strip_provider_prefix(v)
    if PREFERRED_PROVIDER == 'anthropic':
        return f'anthropic/{clean}', True

    name = clean.lower()
    if 'haiku' in name:
        if PREFERRED_PROVIDER == 'google' and SMALL_MODEL in GEMINI_MODELS:
            return f'gemini/{SMALL_MODEL}', True
        return f'openai/{SMALL_MODEL}', True
    # 'sonnet' OR 'opus' both route to BIG_MODEL — Archon defaults to
    # 'opus' so we have to recognize it explicitly.
    if 'sonnet' in name or 'opus' in name:
        if PREFERRED_PROVIDER == 'google' and BIG_MODEL in GEMINI_MODELS:
            return f'gemini/{BIG_MODEL}', True
        return f'openai/{BIG_MODEL}', True

    if clean in GEMINI_MODELS and not v.startswith('gemini/'):
        return f'gemini/{clean}', True
    if clean in OPENAI_MODELS and not v.startswith('openai/'):
        return f'openai/{clean}', True
    return v, False


# ── pydantic shapes (Anthropic API) ───────────────────────────────────


class ContentBlockText(BaseModel):
    type: Literal['text']
    text: str


class ContentBlockImage(BaseModel):
    type: Literal['image']
    source: Dict[str, Any]


class ContentBlockToolUse(BaseModel):
    type: Literal['tool_use']
    id: str
    name: str
    input: Dict[str, Any]


class ContentBlockToolResult(BaseModel):
    type: Literal['tool_result']
    tool_use_id: str
    content: Union[str, List[Dict[str, Any]], Dict[str, Any], List[Any], Any]


class SystemContent(BaseModel):
    type: Literal['text']
    text: str


class Message(BaseModel):
    role: Literal['user', 'assistant']
    content: Union[str, List[Union[ContentBlockText, ContentBlockImage, ContentBlockToolUse, ContentBlockToolResult]]]


class Tool(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any]


class ThinkingConfig(BaseModel):
    enabled: bool = True


class MessagesRequest(BaseModel):
    model: str
    max_tokens: int
    messages: List[Message]
    system: Optional[Union[str, List[SystemContent]]] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    tools: Optional[List[Tool]] = None
    tool_choice: Optional[Dict[str, Any]] = None
    thinking: Optional[ThinkingConfig] = None
    original_model: Optional[str] = None

    @field_validator('model')
    def _map(cls, v, info):
        original = v
        new_model, mapped = _map_model_name(v)
        if mapped:
            logger.debug(f"MODEL MAPPING: '{original}' -> '{new_model}'")
        elif not v.startswith(('openai/', 'gemini/', 'anthropic/')):
            logger.warning(f"No prefix or mapping rule for model: '{original}'. Using as is.")
        if isinstance(info.data, dict):
            info.data['original_model'] = original
        return new_model


class TokenCountRequest(BaseModel):
    model: str
    messages: List[Message]
    system: Optional[Union[str, List[SystemContent]]] = None
    tools: Optional[List[Tool]] = None
    thinking: Optional[ThinkingConfig] = None
    tool_choice: Optional[Dict[str, Any]] = None
    original_model: Optional[str] = None

    @field_validator('model')
    def _map(cls, v, info):
        original = v
        new_model, _ = _map_model_name(v)
        if isinstance(info.data, dict):
            info.data['original_model'] = original
        return new_model


class TokenCountResponse(BaseModel):
    input_tokens: int


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class MessagesResponse(BaseModel):
    id: str
    model: str
    role: Literal['assistant'] = 'assistant'
    content: List[Union[ContentBlockText, ContentBlockToolUse]]
    type: Literal['message'] = 'message'
    stop_reason: Optional[Literal['end_turn', 'max_tokens', 'stop_sequence', 'tool_use']] = None
    stop_sequence: Optional[str] = None
    usage: Usage


# ── translation ───────────────────────────────────────────────────────


def parse_tool_result_content(content) -> str:
    """Flatten an Anthropic ``tool_result.content`` to a plain string."""
    if content is None:
        return 'No content provided'
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = ''
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'text':
                out += item.get('text', '') + '\n'
            elif isinstance(item, str):
                out += item + '\n'
            elif isinstance(item, dict):
                if 'text' in item:
                    out += item.get('text', '') + '\n'
                else:
                    try:
                        out += json.dumps(item) + '\n'
                    except Exception:
                        out += str(item) + '\n'
            else:
                out += str(item) + '\n'
        return out.strip()
    if isinstance(content, dict):
        if content.get('type') == 'text':
            return content.get('text', '')
        try:
            return json.dumps(content)
        except Exception:
            return str(content)
    return str(content)


def convert_anthropic_to_litellm(req: MessagesRequest) -> Dict[str, Any]:
    """Build the LiteLLM request dict for an Anthropic-shaped request."""
    messages: list[dict] = []

    # System.
    if req.system:
        if isinstance(req.system, str):
            messages.append({'role': 'system', 'content': req.system})
        elif isinstance(req.system, list):
            text = ''
            for block in req.system:
                if hasattr(block, 'type') and block.type == 'text':
                    text += block.text + '\n\n'
                elif isinstance(block, dict) and block.get('type') == 'text':
                    text += block.get('text', '') + '\n\n'
            if text:
                messages.append({'role': 'system', 'content': text.strip()})

    # Conversation.
    for msg in req.messages:
        content = msg.content
        if isinstance(content, str):
            messages.append({'role': msg.role, 'content': content})
            continue

        # OpenAI-style: a user turn carrying tool_result blocks gets
        # flattened to a single text user message with the results
        # inline. This is what LiteLLM's OpenAI/Gemini paths expect.
        if msg.role == 'user' and any(
            getattr(b, 'type', None) == 'tool_result' for b in content
        ):
            text = ''
            for block in content:
                if not hasattr(block, 'type'):
                    continue
                if block.type == 'text':
                    text += block.text + '\n'
                elif block.type == 'tool_result':
                    tool_id = getattr(block, 'tool_use_id', '')
                    text += f"Tool result for {tool_id}:\n"
                    text += parse_tool_result_content(getattr(block, 'content', None)) + '\n'
            messages.append({'role': 'user', 'content': text.strip()})
            continue

        processed: list[dict] = []
        for block in content:
            if not hasattr(block, 'type'):
                continue
            if block.type == 'text':
                processed.append({'type': 'text', 'text': block.text})
            elif block.type == 'image':
                processed.append({'type': 'image', 'source': block.source})
            elif block.type == 'tool_use':
                processed.append({
                    'type': 'tool_use',
                    'id': block.id,
                    'name': block.name,
                    'input': block.input,
                })
            elif block.type == 'tool_result':
                tr = {
                    'type': 'tool_result',
                    'tool_use_id': getattr(block, 'tool_use_id', ''),
                }
                raw = getattr(block, 'content', None)
                if isinstance(raw, str):
                    tr['content'] = [{'type': 'text', 'text': raw}]
                elif isinstance(raw, list):
                    tr['content'] = raw
                else:
                    tr['content'] = [{'type': 'text', 'text': str(raw or '')}]
                processed.append(tr)
        messages.append({'role': msg.role, 'content': processed})

    max_tokens = req.max_tokens
    if req.model.startswith('openai/') or req.model.startswith('gemini/'):
        # OpenAI/Gemini cap completion tokens; respect their limit.
        max_tokens = min(max_tokens, 16384)

    out: Dict[str, Any] = {
        'model': req.model,
        'messages': messages,
        'max_completion_tokens': max_tokens,
        'temperature': req.temperature,
        'stream': req.stream,
    }
    # ``thinking`` is Anthropic-only.
    if req.thinking and req.model.startswith('anthropic/'):
        out['thinking'] = req.thinking
    if req.stop_sequences:
        out['stop'] = req.stop_sequences
    if req.top_p:
        out['top_p'] = req.top_p
    if req.top_k:
        out['top_k'] = req.top_k

    if req.tools:
        is_gemini = req.model.startswith('gemini/')
        oai_tools: list[dict] = []
        for t in req.tools:
            d = t.dict() if hasattr(t, 'dict') else dict(t)
            schema = d.get('input_schema', {})
            if is_gemini:
                schema = clean_gemini_schema(schema)
            oai_tools.append({
                'type': 'function',
                'function': {
                    'name': d['name'],
                    'description': d.get('description', ''),
                    'parameters': schema,
                },
            })
        out['tools'] = oai_tools

    if req.tool_choice:
        tc = req.tool_choice if isinstance(req.tool_choice, dict) else req.tool_choice.dict()
        ct = tc.get('type')
        if ct == 'auto':
            out['tool_choice'] = 'auto'
        elif ct == 'any':
            out['tool_choice'] = 'any'
        elif ct == 'tool' and 'name' in tc:
            out['tool_choice'] = {'type': 'function', 'function': {'name': tc['name']}}
        else:
            out['tool_choice'] = 'auto'

    return out


def convert_litellm_to_anthropic(litellm_response, original_request: MessagesRequest) -> MessagesResponse:
    """Translate a LiteLLM response (object or dict) back to Anthropic's shape."""
    try:
        clean_model = _strip_provider_prefix(original_request.model)
        is_claude = clean_model.startswith('claude-')

        if hasattr(litellm_response, 'choices') and hasattr(litellm_response, 'usage'):
            choices = litellm_response.choices
            message = choices[0].message if choices else None
            content_text = getattr(message, 'content', '') if message else ''
            tool_calls = getattr(message, 'tool_calls', None) if message else None
            finish_reason = choices[0].finish_reason if choices else 'stop'
            usage_info = litellm_response.usage
            response_id = getattr(litellm_response, 'id', f'msg_{uuid.uuid4()}')
        else:
            try:
                d = litellm_response if isinstance(litellm_response, dict) else litellm_response.dict()
            except AttributeError:
                d = getattr(litellm_response, 'model_dump', lambda: getattr(litellm_response, '__dict__', {}))()
            choices = d.get('choices', [{}])
            message = choices[0].get('message', {}) if choices else {}
            content_text = message.get('content', '')
            tool_calls = message.get('tool_calls', None)
            finish_reason = choices[0].get('finish_reason', 'stop') if choices else 'stop'
            usage_info = d.get('usage', {})
            response_id = d.get('id', f'msg_{uuid.uuid4()}')

        content: list[dict] = []
        if content_text:
            content.append({'type': 'text', 'text': content_text})

        if tool_calls and is_claude:
            if not isinstance(tool_calls, list):
                tool_calls = [tool_calls]
            for call in tool_calls:
                fn = call.get('function', {}) if isinstance(call, dict) else getattr(call, 'function', None)
                tool_id = (call.get('id') if isinstance(call, dict) else getattr(call, 'id', '')) or f'tool_{uuid.uuid4()}'
                if isinstance(fn, dict):
                    name = fn.get('name', '')
                    args = fn.get('arguments', '{}')
                else:
                    name = getattr(fn, 'name', '') if fn else ''
                    args = getattr(fn, 'arguments', '{}') if fn else '{}'
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {'raw': args}
                content.append({'type': 'tool_use', 'id': tool_id, 'name': name, 'input': args})
        elif tool_calls:
            # Non-Claude target: flatten the tool calls into the text body.
            extra = '\n\nTool usage:\n'
            if not isinstance(tool_calls, list):
                tool_calls = [tool_calls]
            for call in tool_calls:
                fn = call.get('function', {}) if isinstance(call, dict) else getattr(call, 'function', None)
                if isinstance(fn, dict):
                    name = fn.get('name', '')
                    args = fn.get('arguments', '{}')
                else:
                    name = getattr(fn, 'name', '') if fn else ''
                    args = getattr(fn, 'arguments', '{}') if fn else '{}'
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        pass
                args_str = json.dumps(args, indent=2) if not isinstance(args, str) else args
                extra += f"Tool: {name}\nArguments: {args_str}\n\n"
            if content and content[0]['type'] == 'text':
                content[0]['text'] += extra
            else:
                content.append({'type': 'text', 'text': extra})

        if isinstance(usage_info, dict):
            prompt_tokens = usage_info.get('prompt_tokens', 0)
            completion_tokens = usage_info.get('completion_tokens', 0)
        else:
            prompt_tokens = getattr(usage_info, 'prompt_tokens', 0)
            completion_tokens = getattr(usage_info, 'completion_tokens', 0)

        stop_map = {'stop': 'end_turn', 'length': 'max_tokens', 'tool_calls': 'tool_use'}
        stop_reason = stop_map.get(finish_reason, 'end_turn')

        if not content:
            content.append({'type': 'text', 'text': ''})

        return MessagesResponse(
            id=response_id,
            model=original_request.model,
            role='assistant',
            content=content,
            stop_reason=stop_reason,
            stop_sequence=None,
            usage=Usage(input_tokens=prompt_tokens, output_tokens=completion_tokens),
        )
    except Exception as e:
        import traceback
        logger.error(f"convert_litellm_to_anthropic failed: {e}\n{traceback.format_exc()}")
        return MessagesResponse(
            id=f'msg_{uuid.uuid4()}',
            model=original_request.model,
            role='assistant',
            content=[{'type': 'text', 'text': f'Error converting response: {e}'}],
            stop_reason='end_turn',
            usage=Usage(input_tokens=0, output_tokens=0),
        )


# ── streaming ─────────────────────────────────────────────────────────


async def handle_streaming(response_generator, original_request: MessagesRequest):
    """Yield Anthropic-shaped SSE events from a LiteLLM stream."""
    try:
        message_id = f"msg_{uuid.uuid4().hex[:24]}"
        message_data = {
            'type': 'message_start',
            'message': {
                'id': message_id, 'type': 'message', 'role': 'assistant',
                'model': original_request.model, 'content': [],
                'stop_reason': None, 'stop_sequence': None,
                'usage': {
                    'input_tokens': 0, 'cache_creation_input_tokens': 0,
                    'cache_read_input_tokens': 0, 'output_tokens': 0,
                },
            },
        }
        yield f"event: message_start\ndata: {json.dumps(message_data)}\n\n"
        yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
        yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"

        tool_index = None
        accumulated_text = ''
        text_sent = False
        text_block_closed = False
        output_tokens = 0
        has_sent_stop_reason = False
        last_tool_index = 0

        async for chunk in response_generator:
            try:
                if hasattr(chunk, 'usage') and chunk.usage is not None:
                    if hasattr(chunk.usage, 'completion_tokens'):
                        output_tokens = chunk.usage.completion_tokens

                if not (hasattr(chunk, 'choices') and chunk.choices):
                    continue
                choice = chunk.choices[0]
                delta = getattr(choice, 'delta', getattr(choice, 'message', {}))
                finish_reason = getattr(choice, 'finish_reason', None)

                delta_content = getattr(delta, 'content', None) if not isinstance(delta, dict) else delta.get('content')
                if delta_content:
                    accumulated_text += delta_content
                    if tool_index is None and not text_block_closed:
                        text_sent = True
                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': delta_content}})}\n\n"

                delta_tool_calls = getattr(delta, 'tool_calls', None) if not isinstance(delta, dict) else delta.get('tool_calls')
                if delta_tool_calls:
                    if tool_index is None:
                        if text_sent and not text_block_closed:
                            text_block_closed = True
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                        elif accumulated_text and not text_sent and not text_block_closed:
                            text_sent = True
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': accumulated_text}})}\n\n"
                            text_block_closed = True
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                        elif not text_block_closed:
                            text_block_closed = True
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                    if not isinstance(delta_tool_calls, list):
                        delta_tool_calls = [delta_tool_calls]
                    for tc in delta_tool_calls:
                        idx = tc.get('index') if isinstance(tc, dict) else getattr(tc, 'index', 0) or 0
                        if tool_index is None or idx != tool_index:
                            tool_index = idx
                            last_tool_index += 1
                            anthropic_tool_index = last_tool_index
                            fn = tc.get('function', {}) if isinstance(tc, dict) else getattr(tc, 'function', None)
                            name = fn.get('name', '') if isinstance(fn, dict) else (getattr(fn, 'name', '') if fn else '')
                            tool_id = (tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', '')) or f"toolu_{uuid.uuid4().hex[:24]}"
                            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': anthropic_tool_index, 'content_block': {'type': 'tool_use', 'id': tool_id, 'name': name, 'input': {}}})}\n\n"
                        fn = tc.get('function', {}) if isinstance(tc, dict) else getattr(tc, 'function', None)
                        args = fn.get('arguments', '') if isinstance(fn, dict) else (getattr(fn, 'arguments', '') if fn else '')
                        if args:
                            try:
                                if isinstance(args, dict):
                                    args_json = json.dumps(args)
                                else:
                                    json.loads(args)
                                    args_json = args
                            except (json.JSONDecodeError, TypeError):
                                args_json = args
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': last_tool_index, 'delta': {'type': 'input_json_delta', 'partial_json': args_json}})}\n\n"

                if finish_reason and not has_sent_stop_reason:
                    has_sent_stop_reason = True
                    if tool_index is not None:
                        for i in range(1, last_tool_index + 1):
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"
                    if not text_block_closed:
                        if accumulated_text and not text_sent:
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': accumulated_text}})}\n\n"
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                    stop_reason = {'length': 'max_tokens', 'tool_calls': 'tool_use', 'stop': 'end_turn'}.get(finish_reason, 'end_turn')
                    usage = {'output_tokens': output_tokens}
                    yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': usage})}\n\n"
                    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
            except Exception as e:
                logger.error(f"streaming chunk error: {e}")
                continue

        # Stream ended without explicit finish_reason — close cleanly.
        if not has_sent_stop_reason:
            if tool_index is not None:
                for i in range(1, last_tool_index + 1):
                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
            usage = {'output_tokens': output_tokens}
            yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': usage})}\n\n"
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
            yield "data: [DONE]\n\n"
    except Exception as e:
        import traceback
        logger.error(f"stream error: {e}\n{traceback.format_exc()}")
        yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'error', 'stop_sequence': None}, 'usage': {'output_tokens': 0}})}\n\n"
        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
        yield "data: [DONE]\n\n"


# ── FastAPI app ───────────────────────────────────────────────────────


app = FastAPI()


@app.middleware('http')
async def log_requests(request: Request, call_next):
    logger.debug(f"Request: {request.method} {request.url.path}")
    return await call_next(request)


@app.post('/v1/messages')
async def create_message(request: MessagesRequest, raw_request: Request):
    try:
        body = await raw_request.body()
        body_json = json.loads(body.decode('utf-8'))
        original_model = body_json.get('model', 'unknown')
        display_model = original_model.split('/')[-1] if '/' in original_model else original_model

        litellm_request = convert_anthropic_to_litellm(request)

        if request.model.startswith('openai/'):
            litellm_request['api_key'] = OPENAI_API_KEY
            if OPENAI_BASE_URL:
                litellm_request['api_base'] = OPENAI_BASE_URL
        elif request.model.startswith('gemini/'):
            litellm_request['api_key'] = GEMINI_API_KEY
        else:
            litellm_request['api_key'] = ANTHROPIC_API_KEY

        # OpenAI / Gemini don't accept Anthropic content-block lists in
        # message bodies — flatten any remaining structures to strings.
        if 'openai' in litellm_request['model'] or 'gemini' in litellm_request['model']:
            for i, msg in enumerate(litellm_request['messages']):
                content = msg.get('content')
                if isinstance(content, list):
                    text = ''
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        bt = block.get('type')
                        if bt == 'text':
                            text += block.get('text', '') + '\n'
                        elif bt == 'tool_result':
                            tid = block.get('tool_use_id', 'unknown')
                            text += f"[Tool Result ID: {tid}]\n"
                            text += parse_tool_result_content(block.get('content', '')) + '\n'
                        elif bt == 'tool_use':
                            tname = block.get('name', 'unknown')
                            tid = block.get('id', 'unknown')
                            text += f"[Tool: {tname} (ID: {tid})]\nInput: {json.dumps(block.get('input', {}))}\n\n"
                        elif bt == 'image':
                            text += '[Image content - not displayed in text format]\n'
                    litellm_request['messages'][i]['content'] = text.strip() or '...'
                elif content is None:
                    litellm_request['messages'][i]['content'] = '...'
                # Strip any keys OpenAI / Gemini don't recognize.
                for key in list(msg.keys()):
                    if key not in ('role', 'content', 'name', 'tool_call_id', 'tool_calls'):
                        del msg[key]

        num_tools = len(request.tools) if request.tools else 0
        log_request_beautifully('POST', raw_request.url.path, display_model, litellm_request['model'], len(litellm_request['messages']), num_tools, 200)

        if request.stream:
            response_generator = await litellm.acompletion(**litellm_request)
            return StreamingResponse(handle_streaming(response_generator, request), media_type='text/event-stream')
        litellm_response = litellm.completion(**litellm_request)
        return convert_litellm_to_anthropic(litellm_response, request)
    except Exception as e:
        import traceback
        logger.error(f"create_message: {e}\n{traceback.format_exc()}")
        status = getattr(e, 'status_code', 500)
        raise HTTPException(status_code=status, detail=f"Error: {e}")


@app.post('/v1/messages/count_tokens')
async def count_tokens(request: TokenCountRequest, raw_request: Request):
    try:
        original_model = request.original_model or request.model
        display_model = original_model.split('/')[-1] if '/' in original_model else original_model

        converted = convert_anthropic_to_litellm(MessagesRequest(
            model=request.model, max_tokens=100, messages=request.messages,
            system=request.system, tools=request.tools,
            tool_choice=request.tool_choice, thinking=request.thinking,
        ))
        try:
            from litellm import token_counter
            num_tools = len(request.tools) if request.tools else 0
            log_request_beautifully('POST', raw_request.url.path, display_model, converted['model'], len(converted['messages']), num_tools, 200)
            args: Dict[str, Any] = {'model': converted['model'], 'messages': converted['messages']}
            if request.model.startswith('openai/') and OPENAI_BASE_URL:
                args['api_base'] = OPENAI_BASE_URL
            return TokenCountResponse(input_tokens=token_counter(**args))
        except ImportError:
            logger.error('litellm.token_counter unavailable')
            return TokenCountResponse(input_tokens=1000)
    except Exception as e:
        import traceback
        logger.error(f"count_tokens: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error counting tokens: {e}")


@app.get('/')
async def root():
    return {'message': 'Archon Anthropic↔LiteLLM proxy'}


# ── pretty per-request log line ───────────────────────────────────────


class _Colors:
    CYAN = '\033[96m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def log_request_beautifully(method, path, claude_model, openai_model, num_messages, num_tools, status_code):
    claude_display = f"{_Colors.CYAN}{claude_model}{_Colors.RESET}"
    endpoint = path.split('?', 1)[0]
    openai_display = openai_model.split('/')[-1] if '/' in openai_model else openai_model
    openai_display = f"{_Colors.GREEN}{openai_display}{_Colors.RESET}"
    tools_str = f"{_Colors.MAGENTA}{num_tools} tools{_Colors.RESET}"
    messages_str = f"{_Colors.BLUE}{num_messages} messages{_Colors.RESET}"
    status_str = f"{_Colors.GREEN}OK {status_code}{_Colors.RESET}" if status_code == 200 else f"{_Colors.RED}{status_code}{_Colors.RESET}"
    print(f"{_Colors.BOLD}{method} {endpoint}{_Colors.RESET} {status_str}")
    print(f"{claude_display} -> {openai_display} {tools_str} {messages_str}")
    sys.stdout.flush()


# ── entrypoint ────────────────────────────────────────────────────────


def main() -> None:
    """``python -m archon.proxy.server`` — port from ARCHON_PROXY_PORT."""
    port = int(os.environ.get('ARCHON_PROXY_PORT', '8082'))
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='error')


if __name__ == '__main__':
    main()

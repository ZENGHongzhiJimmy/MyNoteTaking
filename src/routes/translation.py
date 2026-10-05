import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Blueprint, jsonify, request

translation_bp = Blueprint('translation', __name__)

LANGUAGES = {
    'Chinese (Simplified)': 'Chinese (Simplified)',
    'Japanese': 'Japanese',
}


def _get_api_key():
    api_key = os.environ.get('API')
    if api_key:
        return api_key

    env_path = Path(__file__).resolve().parents[2] / '.env'
    if not env_path.is_file():
        return None

    for line in env_path.read_text(encoding='utf-8').splitlines():
        key, separator, value = line.partition('=')
        if separator and key.strip() == 'API':
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value or None
    return None


@translation_bp.route('/translate', methods=['POST'])
def translate_note():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON request body is required'}), 400

    title = data.get('title', '')
    content = data.get('content', '')
    target_language = data.get('target_language')

    if not isinstance(title, str) or not isinstance(content, str):
        return jsonify({'error': 'Title and content must be text'}), 400
    if not title.strip() and not content.strip():
        return jsonify({'error': 'Enter a title or note content to translate'}), 400
    if not isinstance(target_language, str) or target_language not in LANGUAGES:
        return jsonify({'error': 'Choose a supported target language'}), 400

    api_key = _get_api_key()
    if not api_key:
        return jsonify({'error': 'Translation is not configured. Set API in the root .env file.'}), 503

    messages = [
        {
            'role': 'system',
            'content': (
                f'Translate the note title and content into {LANGUAGES[target_language]}. '
                'Preserve the original meaning, tone, and formatting. Return only a JSON object '
                'with string fields "title" and "content".'
            ),
        },
        {
            'role': 'user',
            'content': json.dumps({'title': title, 'content': content}, ensure_ascii=False),
        },
    ]
    payload = json.dumps({
        'model': 'openrouter/free',
        'messages': messages,
        'response_format': {'type': 'json_object'},
    }).encode('utf-8')
    provider_request = Request(
        'https://openrouter.ai/api/v1/chat/completions',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    try:
        with urlopen(provider_request, timeout=45) as response:
            provider_data = json.loads(response.read().decode('utf-8'))
    except HTTPError as error:
        try:
            error_body = json.loads(error.read().decode('utf-8'))
            provider_message = error_body.get('error', {}).get('message')
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            provider_message = None

        if isinstance(provider_message, str):
            provider_message = provider_message.replace(api_key, '[redacted]')[:300]

        if error.code == 401:
            message = 'OpenRouter rejected the API key. Check that API in the root .env is a valid OpenRouter key.'
        elif error.code == 402:
            message = (
                'OpenRouter rejected the free-model request. Check the account’s free-model '
                'limits and API key permissions.'
            )
        elif error.code == 403:
            message = (
                'OpenRouter denied this request. Check that API in the root .env is a valid, '
                'unrestricted OpenRouter key and that the account can access free models.'
            )
        elif error.code == 404:
            message = 'OpenRouter could not route this request to an available free model.'
        elif error.code == 429:
            message = 'OpenRouter rate limit reached. Please wait and try again.'
        else:
            message = f'Translation provider returned HTTP {error.code}.'
        if provider_message:
            message = f'{message} Details: {provider_message}'
        return jsonify({'error': message}), 502
    except URLError:
        return jsonify({'error': 'Could not connect to the translation provider'}), 502
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jsonify({'error': 'Translation provider returned an invalid response'}), 502

    try:
        translated = json.loads(provider_data['choices'][0]['message']['content'])
        translated_title = translated['title']
        translated_content = translated['content']
        if not isinstance(translated_title, str) or not isinstance(translated_content, str):
            raise ValueError('Translated fields must be text')
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return jsonify({'error': 'Translation provider returned an invalid translation'}), 502

    return jsonify({'title': translated_title, 'content': translated_content})

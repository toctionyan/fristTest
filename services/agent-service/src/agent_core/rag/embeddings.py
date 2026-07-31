import math
import re
from collections import Counter

TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    raw = TOKEN_RE.findall(text.lower())
    tokens: list[str] = []
    for item in raw:
        if len(item) <= 2:
            tokens.append(item)
        else:
            # 中文和英文都做简单 ngram，避免没有 embedding 服务时完全不可用
            tokens.extend(item[i:i+2] for i in range(len(item)-1))
            tokens.append(item)
    return tokens


def sparse_vector(text: str) -> dict[str, float]:
    counts = Counter(tokenize(text))
    norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
    return {k: v / norm for k, v in counts.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())

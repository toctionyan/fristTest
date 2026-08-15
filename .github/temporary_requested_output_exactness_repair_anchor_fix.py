from pathlib import Path

path = Path('.github/temporary_requested_output_exactness_repair.py')
text = path.read_text(encoding='utf-8')
old = '''goal = replace_once(\n    goal,\n    '                    "business effect. An unsupported/unregistered effect or harmless naming granularity is not "\\n                    "itself a mismatch, and capability availability must not be used as evidence. Withdraw the mismatch only when the "',\n    '                    "business effect. An unsupported/unregistered effect or harmless naming granularity is not "\\n                    "itself a mismatch, but a registered requested_outputs identity whose CANONICAL_SEMANTIC_OUTPUT_VOCABULARY description does not cover the literal requested information dimension/outcome is a real mismatch; when no registered description matches exactly, open is the only faithful identity. Capability availability must not be used as evidence. Withdraw the mismatch only when the "',\n    "strengthen requested-effect reaudit",\n)'''
new = '''goal = replace_once(\n    goal,\n    '"itself a mismatch, and capability availability must not be used as evidence. Withdraw the mismatch only when the "',\n    '"itself a mismatch, but a registered requested_outputs identity whose CANONICAL_SEMANTIC_OUTPUT_VOCABULARY description does not cover the literal requested information dimension/outcome is a real mismatch; when no registered description matches exactly, open is the only faithful identity. Capability availability must not be used as evidence. Withdraw the mismatch only when the "',\n    "strengthen requested-effect reaudit",\n)'''
if text.count(old) != 1:
    raise RuntimeError(f'expected one helper anchor block, got {text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('helper anchor repaired')

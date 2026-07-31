"""Small runtime smoke set for the console evaluation endpoint.

Strong-context adversarial evaluation lives in tests/eval_cases; this list only
keeps the web console's manual evaluation endpoint operational.
"""

EVAL_CASES = [
    {"message": "我买过什么？", "user_id": "u001", "expected_type": "answer"},
    {"message": "查一下我的优惠券", "user_id": "u001", "expected_type": "answer"},
]

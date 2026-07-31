#!/usr/bin/env python3
"""Non-mutating smoke check through the installed EcommerceModule adapter."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from agent_core.business.contracts import ActorContext  # noqa: E402
from agent_core.business.transport import BusinessServiceError  # noqa: E402
from agent_modules.ecommerce.business_port import get_ecommerce_business_port  # noqa: E402


def main() -> int:
    get_ecommerce_business_port.cache_clear()
    try:
        response = get_ecommerce_business_port().read_resource(
            ActorContext(user_id="u001", role="customer", tenant_id="default"),
            resource_type="order",
            resource_id="10002",
            query={"user_id": "u001"},
        )
    except BusinessServiceError as exc:
        print(f"External Business Service smoke: FAILED ({exc})")
        return 1
    if not response.get("success") or response.get("data", {}).get("order_id") != "10002":
        print(f"External Business Service smoke: FAILED ({response})")
        return 1
    print("External Business Service smoke: PASSED")
    print(
        "EcommerceModule BusinessPort reached the external service and read order "
        f"{response['data']['order_id']}: {response['data'].get('product_name', '')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

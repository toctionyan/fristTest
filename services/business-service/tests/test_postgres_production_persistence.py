from __future__ import annotations

import os
from uuid import uuid4

import pytest

from business_service.application.service import BusinessService
from business_service.database import PostgresBusinessDatabase, utcnow
from business_service.security import Actor


@pytest.mark.integration
def test_postgres_business_facts_and_idempotency_are_shared_across_instances() -> None:
    url = os.getenv("BUSINESS_TEST_POSTGRES_URL") or os.getenv("AGENT_TEST_POSTGRES_URL")
    if not url:
        pytest.fail("BUSINESS_TEST_POSTGRES_URL or AGENT_TEST_POSTGRES_URL is required")
    db_a = PostgresBusinessDatabase(url)
    db_b = PostgresBusinessDatabase(url)
    db_a.initialize()
    suffix = uuid4().hex[:12]
    user_id = f"pg-user-{suffix}"
    product_id = f"pg-product-{suffix}"
    order_id = f"pg-order-{suffix}"
    key = f"pg-idempotency-{suffix}"
    refund_id = f"pg-refund-{suffix}"
    now = utcnow()
    try:
        with db_a.transaction() as conn:
            conn.execute(
                "INSERT INTO accounts(user_id,tenant_id,role,display_name,permissions_json,created_at) VALUES(?,?,?,?,?,?)",
                (user_id, "tenant-pg", "customer", "Postgres Test", "[]", now),
            )
            conn.execute(
                "INSERT INTO products(product_id,product_name,price,stock,updated_at) VALUES(?,?,?,?,?)",
                (product_id, "Shared product", 10.0, 1, now),
            )
            conn.execute(
                "INSERT INTO orders(order_id,user_id,tenant_id,product_id,product_name,status,amount,shipping_address,created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, user_id, "tenant-pg", product_id, "Shared product", "已付款", 10.0, "test", now, now, 1),
            )
            conn.execute(
                "INSERT INTO idempotency_records(tenant_id,actor_user_id,command_name,idempotency_key,request_hash,response_json,created_at) VALUES(?,?,?,?,?,?,?)",
                ("tenant-pg", user_id, "test", key, "hash", "{}", now),
            )
            conn.execute(
                "INSERT INTO refunds(refund_id,order_id,user_id,subject_user_id,created_by_actor_id,tenant_id,reason,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (refund_id, order_id, user_id, user_id, user_id, "tenant-pg", "test", "待审核", 1, now, now),
            )

        with db_b.read() as conn:
            order = conn.execute(
                "SELECT status,version FROM orders WHERE order_id=?", (order_id,)
            ).fetchone()
            record = conn.execute(
                "SELECT request_hash FROM idempotency_records WHERE tenant_id=? AND actor_user_id=? AND command_name=? AND idempotency_key=?",
                ("tenant-pg", user_id, "test", key),
            ).fetchone()
        assert order == {"status": "已付款", "version": 1}
        assert record == {"request_hash": "hash"}
        actor = Actor(
            user_id=user_id,
            role="customer",
            tenant_id="tenant-pg",
            account_id=user_id,
            permissions=frozenset(),
        )
        listed = BusinessService(db_b).list_resources(
            actor,
            "refund",
            order_id=order_id,
        )
        assert [item["refund_id"] for item in listed["data"]] == [refund_id]
    finally:
        with db_a.transaction() as conn:
            conn.execute("DELETE FROM refunds WHERE refund_id=?", (refund_id,))
            conn.execute("DELETE FROM idempotency_records WHERE idempotency_key=?", (key,))
            conn.execute("DELETE FROM orders WHERE order_id=?", (order_id,))
            conn.execute("DELETE FROM products WHERE product_id=?", (product_id,))
            conn.execute("DELETE FROM accounts WHERE user_id=?", (user_id,))


@pytest.mark.integration
def test_postgres_all_order_filtered_resource_queries_run_through_domain_contract() -> None:
    """Exercise the real PostgreSQL resource-list path for every order-bound resource.

    This prevents a PostgreSQL provider from looking complete while a generic
    Business query still contains SQLite-only schema introspection.
    """
    url = os.getenv("BUSINESS_TEST_POSTGRES_URL") or os.getenv("AGENT_TEST_POSTGRES_URL")
    if not url:
        pytest.fail("BUSINESS_TEST_POSTGRES_URL or AGENT_TEST_POSTGRES_URL is required")
    db = PostgresBusinessDatabase(url)
    db.initialize()
    suffix = uuid4().hex[:12]
    tenant_id = f"pg-tenant-{suffix}"
    user_id = f"pg-user-{suffix}"
    product_id = f"pg-product-{suffix}"
    order_id = f"pg-order-{suffix}"
    refund_id = f"pg-refund-{suffix}"
    ticket_id = f"pg-ticket-{suffix}"
    invoice_id = f"pg-invoice-{suffix}"
    now = utcnow()
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO accounts(user_id,tenant_id,role,display_name,permissions_json,created_at) VALUES(?,?,?,?,?,?)",
                (user_id, tenant_id, "customer", "Postgres Query Test", "[]", now),
            )
            conn.execute(
                "INSERT INTO products(product_id,product_name,price,stock,updated_at) VALUES(?,?,?,?,?)",
                (product_id, "Postgres query product", 20.0, 1, now),
            )
            conn.execute(
                "INSERT INTO orders(order_id,user_id,tenant_id,product_id,product_name,status,amount,shipping_address,created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, user_id, tenant_id, product_id, "Postgres query product", "已付款", 20.0, "test", now, now, 1),
            )
            conn.execute(
                "INSERT INTO refunds(refund_id,order_id,user_id,subject_user_id,created_by_actor_id,tenant_id,reason,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (refund_id, order_id, user_id, user_id, user_id, tenant_id, "test", "待审核", 1, now, now),
            )
            conn.execute(
                "INSERT INTO after_sales_tickets(ticket_id,order_id,user_id,subject_user_id,created_by_actor_id,tenant_id,reason,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ticket_id, order_id, user_id, user_id, user_id, tenant_id, "test", "待审核", 1, now, now),
            )
            conn.execute(
                "INSERT INTO invoices(invoice_id,order_id,user_id,subject_user_id,created_by_actor_id,tenant_id,invoice_title,amount,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (invoice_id, order_id, user_id, user_id, user_id, tenant_id, "Postgres query invoice", 20.0, "待开票", 1, now, now),
            )

        actor = Actor(
            user_id=user_id,
            role="customer",
            tenant_id=tenant_id,
            account_id=user_id,
            permissions=frozenset(),
        )
        service = BusinessService(db)
        expected = {
            "refund": ("refund_id", refund_id),
            "after_sales": ("ticket_id", ticket_id),
            "invoice": ("invoice_id", invoice_id),
        }
        for resource_type, (id_field, expected_id) in expected.items():
            result = service.list_resources(actor, resource_type, order_id=order_id)
            assert [item[id_field] for item in result["data"]] == [expected_id]
    finally:
        with db.transaction() as conn:
            conn.execute("DELETE FROM invoices WHERE invoice_id=?", (invoice_id,))
            conn.execute("DELETE FROM after_sales_tickets WHERE ticket_id=?", (ticket_id,))
            conn.execute("DELETE FROM refunds WHERE refund_id=?", (refund_id,))
            conn.execute("DELETE FROM orders WHERE order_id=?", (order_id,))
            conn.execute("DELETE FROM products WHERE product_id=?", (product_id,))
            conn.execute("DELETE FROM accounts WHERE user_id=?", (user_id,))

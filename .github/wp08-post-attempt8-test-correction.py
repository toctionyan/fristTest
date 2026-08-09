from pathlib import Path

path = Path("candidate/skill-system/tests/test_wp08_post_attempt8_repair.py")
text = path.read_text(encoding="utf-8")
old = '''        source = (ROOT / "scripts/verify_product_browser_journey.py").read_text(encoding="utf-8")
        self.assertIn("build_sqlalchemy_store_provider(DatabaseSettings(", source)
        self.assertIn('provider.traces.list_recent_by_event_type(', source)
        self.assertNotIn("psycopg.connect", source)
        self.assertNotIn("FROM trace_logs", source)
'''
new = '''        source = (ROOT / "scripts/verify_product_browser_journey.py").read_text(encoding="utf-8")
        postgres_source = source.split("def _postgres_graph_diagnostics", 1)[1].split(
            "def _configured_model_preflight", 1
        )[0]
        self.assertIn("build_sqlalchemy_store_provider(DatabaseSettings(", postgres_source)
        self.assertIn('provider.traces.list_recent_by_event_type(', postgres_source)
        self.assertNotIn("psycopg.connect", postgres_source)
        self.assertNotIn("FROM trace_logs", postgres_source)
'''
if text.count(old) != 1:
    raise SystemExit(f"test_anchor_count:{text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

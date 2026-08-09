#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

source_path = Path(__file__).with_name("wp08-new-release-attempt6-environment-runtime-repair-publisher.py")
source = source_path.read_text(encoding="utf-8")
old = '''replace_once(
    workflow_path,
    '        if: ${{ vars.WP08_RESUME_RUN_ID != \'\' }}\\n',
    '        if: ${{ env.WP08_RESUME_RUN_ID_RESOLVED != \'\' }}\\n',
)
'''
new = '''workflow_text = workflow_path.read_text(encoding="utf-8")
legacy_resume_if = '        if: ${{ vars.WP08_RESUME_RUN_ID != \'\' }}\\n'
resolved_resume_if = '        if: ${{ env.WP08_RESUME_RUN_ID_RESOLVED != \'\' }}\\n'
if workflow_text.count(legacy_resume_if) != 2:
    raise SystemExit(f"expected two legacy resume conditions, found {workflow_text.count(legacy_resume_if)}")
workflow_path.write_text(workflow_text.replace(legacy_resume_if, resolved_resume_if, 1), encoding="utf-8")
'''
if source.count(old) != 1:
    raise SystemExit(f"expected publisher workflow condition block once, found {source.count(old)}")
source = source.replace(old, new, 1)
exec(compile(source, str(source_path), "exec"), {"__name__": "__main__", "__file__": str(source_path)})

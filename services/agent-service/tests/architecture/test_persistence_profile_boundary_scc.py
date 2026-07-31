from __future__ import annotations
import ast, json, subprocess, sys
from pathlib import Path
from tests.support.paths import workspace_root
from tests.support.dependency_debt import assert_dependency_debt_monotonic

def _imports(path: Path)->set[str]:
    tree=ast.parse(path.read_text(encoding='utf-8')); out=set()
    for n in ast.walk(tree):
        if isinstance(n,ast.ImportFrom) and n.module and n.module.startswith('agent_core'): out.add(n.module)
        elif isinstance(n,ast.Import): out.update(a.name for a in n.names if a.name.startswith('agent_core'))
    return out

def test_persistence_uses_kernel_profile_and_exits_main_scc(monkeypatch):
    root=workspace_root(__file__); service=root/'services/agent-service'; core=service/'src/agent_core'; persistence=core/'persistence'
    forbidden=[]
    for p in persistence.rglob('*.py'):
        for m in _imports(p):
            if m=='agent_core.runtime' or m.startswith('agent_core.runtime.'): forbidden.append(f'{p.relative_to(root)} -> {m}')
    assert forbidden==[]
    assert (core/'kernel/profile.py').exists()
    runtime_text=(core/'runtime/profile.py').read_text(encoding='utf-8')
    assert 'class RuntimeProfile' not in runtime_text
    sys.path.insert(0,str(service/'src'))
    try:
        import agent_core.kernel.profile as kp
        import agent_core.runtime.profile as rp
        assert rp.RuntimeProfile is kp.RuntimeProfile
        assert rp.get_runtime_profile is kp.get_runtime_profile
        monkeypatch.setenv('APP_PROFILE','preprod')
        monkeypatch.delenv('AGENT_DB_BACKEND', raising=False)
        monkeypatch.delenv('DATABASE_BACKEND', raising=False)
        monkeypatch.delenv('AGENT_DB_CREATE_SCHEMA', raising=False)
        monkeypatch.setenv('AGENT_DATABASE_URL','postgresql+psycopg://agent:secret@db.example/agent')
        assert kp.require_runtime_profile() is kp.RuntimeProfile.PREPROD
        from agent_core.persistence.database_settings import get_database_settings
        settings=get_database_settings()
        assert settings.normalized_backend=='postgres'
        assert settings.database_url.startswith('postgresql+psycopg://')
        assert settings.create_schema is False
    finally: sys.path.pop(0)
    completed=subprocess.run([sys.executable,'-B',str(root/'architecture-skill/scripts/verify_convergence.py'),'--workspace-root',str(root)],cwd=root,text=True,capture_output=True)
    assert completed.returncode==0, completed.stderr or completed.stdout
    payload=json.loads(completed.stdout); debt=payload['checks']['dependency_cycle_debt']
    assert_dependency_debt_monotonic(
        payload, removed_member="persistence", maximum_current_members=4
    )
    assert all('persistence' not in c for c in debt['current_cycles'])
    prior=('observability','storage','context','modules','kernel','resources','ledger','rag','utils')
    assert all(all(x not in c for x in prior) for c in debt['current_cycles'])

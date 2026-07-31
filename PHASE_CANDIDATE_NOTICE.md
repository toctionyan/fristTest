# B17j CI Profile Boundary Repair Phase Candidate

Status: `PHASE_CANDIDATE_ENVIRONMENT_EXECUTION_PENDING`

B17j repairs a real GitHub CI orchestration defect found by a zero-file-difference PR. The four Skill self-validation profiles passed, but the project-wide `quality` Workflow also executed `project-compatibility-smoke`, whose only valid purpose is proving that a Skill-only change did not alter product source.

Project CI now runs Skill static, unit, host-integration and security profiles directly. The Skill-only compatibility guard remains unchanged inside `skill-control-plane` and `skill-release`. GitHub run `30608910835` proved this boundary and then exposed three stale adversarial Harness tests; B17j round 2 repairs only those test contracts. No customer-agent runtime behavior changed.

The candidate is not production closed. Protected `main`, the `production-certification` Environment, production secrets and one successful same-run release execution are still required.

B17j 第 3 轮补齐当前阶段 Changelog，并隔离两个“无 CI 上下文”子进程测试继承的 GitHub Runner 变量；生产发布合同未修改。

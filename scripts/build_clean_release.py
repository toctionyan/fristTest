#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from release_artifact import (
    build_frontend,
    copy_release_sources,
    create_zip,
    create_evidence_bundle,
    load_evidence_summary,
    run_release_self_checks,
    sha256_file,
    verify_release_tree,
    verify_zip,
    write_release_metadata,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--evidence-dir")
    parser.add_argument("--frontend-mode", choices=["npm-ci", "existing-node-modules"], default="npm-ci")
    parser.add_argument(
        "--certification-level",
        choices=["candidate", "protected-release"],
        default="candidate",
    )
    parser.add_argument("--artifact-name")
    args = parser.parse_args()

    workspace = Path(args.workspace_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    version = (workspace / "VERSION").read_text(encoding="utf-8").strip()
    skill = json.loads((workspace / "architecture-skill/manifest.json").read_text(encoding="utf-8")).get("version")
    root_name = args.artifact_name or f"customer_agent_workspace_v{version.replace('.', '_')}_skill_v{str(skill).replace('.', '_')}"
    if args.certification_level == "protected-release":
        if not args.evidence_dir:
            parser.error("protected-release requires --evidence-dir")
        if args.frontend_mode != "npm-ci":
            parser.error("protected-release requires --frontend-mode npm-ci")
        if not (os.getenv("GITHUB_SHA") and os.getenv("GITHUB_RUN_ID")):
            parser.error("protected-release requires GITHUB_SHA and GITHUB_RUN_ID")
    evidence = (
        load_evidence_summary(
            workspace,
            Path(args.evidence_dir).resolve(),
            required_mode="release" if args.certification_level == "protected-release" else None,
        )
        if args.evidence_dir
        else None
    )
    evidence_bundle = None
    if evidence is not None:
        evidence_bundle_path = output_dir / f"{root_name}-quality-evidence.zip"
        evidence_bundle = create_evidence_bundle(
            Path(args.evidence_dir).resolve(), evidence_bundle_path
        )
        evidence["_release_provenance"].update(
            {
                "evidence_bundle_filename": evidence_bundle["filename"],
                "evidence_bundle_sha256": evidence_bundle["sha256"],
            }
        )

    with tempfile.TemporaryDirectory(prefix="clean-release-stage-") as temp:
        stage = Path(temp) / root_name
        stage.mkdir(parents=True)
        copy_release_sources(workspace, stage)
        frontend_mode = build_frontend(workspace, stage, mode=args.frontend_mode)
        manifest = write_release_metadata(
            stage,
            workspace=workspace,
            frontend_build_mode=frontend_mode,
            evidence_summary=evidence,
            certification_level=args.certification_level,
        )
        semantic_result = run_release_self_checks(stage)
        tree_result = verify_release_tree(stage)
        if tree_result["status"] != "PASS":
            print(json.dumps({"stage": tree_result}, ensure_ascii=False, indent=2))
            return 1
        zip_path = output_dir / f"{root_name}.zip"
        create_zip(stage, zip_path, root_name=root_name)
        zip_result = verify_zip(zip_path)
        if zip_result["status"] != "PASS":
            print(json.dumps({"zip": zip_result}, ensure_ascii=False, indent=2))
            return 1
        sidecar = zip_path.with_suffix(zip_path.suffix + ".sha256")
        sidecar.write_text(f"{sha256_file(zip_path)}  {zip_path.name}\n", encoding="utf-8")
        print(json.dumps({
            "status": "PASS",
            "zip": str(zip_path),
            "zip_sha256": sha256_file(zip_path),
            "manifest": manifest,
            "semantic_verification": semantic_result,
            "stage_verification": tree_result,
            "zip_verification": zip_result,
            "evidence_bundle": evidence_bundle,
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

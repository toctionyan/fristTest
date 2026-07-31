#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "skill-system" / "profiles"


def load_profile(name: str) -> dict[str, Any]:
    path = PROFILES / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"unknown Skill profile: {name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("id") != name or payload.get("schema_version") != 1:
        raise ValueError(f"invalid Skill profile: {name}")
    return payload


def expand_profiles(name: str, seen: set[str] | None = None) -> list[dict[str, Any]]:
    seen = set(seen or ())
    if name in seen:
        raise ValueError(f"cyclic Skill profile include: {name}")
    seen.add(name)
    profile = load_profile(name)
    rows: list[dict[str, Any]] = []
    for child in profile.get("includes") or []:
        rows.extend(expand_profiles(str(child), seen.copy()))
    rows.append(profile)
    unique: list[dict[str, Any]] = []
    ids: set[str] = set()
    for row in rows:
        if row["id"] not in ids:
            unique.append(row); ids.add(row["id"])
    return unique


def command_argv(raw: list[str]) -> list[str]:
    return [sys.executable if value == "{python}" else value for value in raw]


def run(name: str) -> dict[str, Any]:
    results=[]
    for profile in expand_profiles(name):
        for index, raw in enumerate(profile.get("commands") or [], start=1):
            argv=command_argv([str(v) for v in raw])
            completed=subprocess.run(argv,cwd=ROOT,text=True,capture_output=True,check=False)
            row={"profile":profile["id"],"command_index":index,"argv":argv,"exit_code":completed.returncode,"stdout":completed.stdout,"stderr":completed.stderr,"status":"PASS" if completed.returncode==0 else "FAIL"}
            results.append(row)
            if completed.returncode:
                return {"status":"FAIL","requested_profile":name,"results":results}
    return {"status":"PASS","requested_profile":name,"results":results}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("profile"); args=parser.parse_args()
    try: result=run(args.profile)
    except (OSError,ValueError,json.JSONDecodeError) as exc: result={"status":"FAIL","requested_profile":args.profile,"error":str(exc),"results":[]}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["status"]=="PASS" else 1

if __name__=="__main__": raise SystemExit(main())

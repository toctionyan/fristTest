#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {'scripts/quality_loop.py': 'c784c0e5ffc771eef1c67ef9446897f691ef91be89d474b3b408df91697cb408', 'scripts/run_production_release.py': 'f0d26a9e69e5ba4f466509a9a2a60f839bf8baa1f63d892f6cf0510540dc6910', 'scripts/verify_production_certification_bundle.py': '83c1207389685d42d07611b44af03c9f483c602efea455bb2371686fe19e0952', 'scripts/verify_production_postgres_bundle.py': '7e11af6c9c04f24687b3c9e27435bdaf335515440e7a362525f1e5cc19bc282a', 'scripts/verify_production_browser_bundle.py': '722f3ae3fa57a61d185860e06ad64910151eb12e445103e079e526e3c14b5579'}
PATCH_B64 = 'eNrNWltT47gSfs+v0PG+xMTxALkAqcqpDeCdoRaSnCRMnS1qyqXYCmhxbI9sA6k5/PfTuvgahwm7PCxVk8SWutVqdX990bh0tULt9j2NEf4UOYyGcfTpe4I9Gm9sLwhCM9yg5a6RBvVd8oK6ne7JUffINLtkuex2HXR0eNjvdhvtdns310ar1XqD86+/onb3yOijFnyeIHikKxTFrDm/mF1NF3P78mqmIz+IEfVRtInMEMcPgwbif+mjSf2IsLh5aGxR6g3UQCsWrFEUJMwhNp8fIboOAwYsI5slfkzXxMYspivsxGJCoyVIvMB5JK4dbuKHwE9p0pcs+JPw6WKQr3IxGS9mk2t7ej0aW3xxNEQFUUBSRvwYfUJa9Eg9rw3Sx2St8RdO4Mcs8DzCtEa6/y12NVrgyuscdo0z1Ooc9oyjQ64/l6yQ7Ydrm7wQJ4nx0iPN54A9RiF2yABNgVJH7X+LH+h/aBz4ZMDlF3TUjwkLAw/HpPmEvQQIQBgDHRiowsNA5Im6xAedupSl79aBK0nEEvCtjgrkAXXsFEtvtPgspelhvZLz6QbS8D0oU9Mld0bihPlICGyuArbGcVOO8L+MbMj1mq9pNNrpFLnAkGs2F8+QQhXHgV7+BOKMP2xKjMC3zk+P75V4EUEa/NIKE4sKExTFF0WOXItD/mGIIz7qdDrcQ+C7B2ednjGYrh09EM9rvu9ouJeQcIBc6sR34nkklyb+0532n9vR9dXiD9v6enVpjS+E6Wnf4Ey2BK4hup5MpvbN5NISFHzFmkmfRwvLvrrMmHJpzHsSNzXqajoKGNI0ZRAluukfiy+TsW3917q4XYzOr62Mw77mYnCGQ/in1/EffbbGC7XKB7M+v51fja35/P3cl0lEfRJFpQX2cykxS9kjjQR6KGfPDJLLOB0tvmQCwVwFVDpqoUACTURCeIDJ8pQEgSEOiVvncafbBeRuwfeZRHBpnSTElNmhh/2CL4I4jESJF99pUYzjJIKVFaz9+G10dW2g8+vJxe/W5WtBTP7HIZL6CcnfMgIOMEAejYQV8x3c5R6dxodadxaD7zhcveic/E9rn2uVVyVsESgvI54A+ErU07b5tTPaNgsCsKeGWxeyuc+DuG7ixDTwQckewREpB++dc1QYX3YwPu45ptnv9ZYrvKoP47u5lAP67nkitPdFaO9LwxBRVc2w4yDwnAdMfVuEP4i9aYhtIvQLWMV3PEBW9/BYqgrAnboQlrKInWIRqFJvlFlzmfhgDDpPmf52Nf5szaazK/Bxa/wV4QjNbsc2B7kFd9HqeFmE9yYE1R2o9GB0sQAz1XJ9tZXIbWmn8ObXY8gBRrcAE7MULCskDuG5CnWweFomvusRTWi73z/h6u73T42TPBfINJfmOFEzSOIwidPIAME9S398vC4EcOFcW3HiG99Po5UFodS64SiDZx90oY6oKT3OCdZr7LsDNCffE35iwl2VOx6o72oIUyC6HcfkgMrmAJMG6AaHIfXv71Roy1irdbk0PmHwUmwJMpyBHNc0bZb4yEnWCeQ79ImgmTwMdA/aitAzhQRJbAhCZRQGEUcRTk/umdA9qDckPheQksgEdo2W5ByzzSBHG2maoCeQBvDELegrZ6WM6UZOmQZRfA9AWWHyRBhdbexV4nm2R1fE2TgesR2gYZmdT6WhzOQRfMGMRw+lzReHhDGyxBdfFHwA3hVEtRmmUXpuGThV5U49UK5orzD1iKtV8HWlSdUpIqSI4FQSzxWYvyTgO9glYBk/QAzTth0PR5Ftwy9uhrb9KkdetS3sBnmGWkWe4iw9PQoB4Pa7E0sZbXMrU0E9jcbvYViJ4Fs8y8YijK5iBU2dn1Sonoz6I64cWkhYBM4rnCdh3jClNuGhoCbBWLIblBn8ki7Dxx1YADwC8mzAaxQ/EMrA+KFUWFMfVqEOn8R9lfE9Uhb4a9CiWeW4eCAor3WAnUPA7SLOkDOIQYNgMRkrhxEB4tiL4Bz5ylFQZclJV9THHsoREp0LTBSMFPdgBYAJjERO6iG5CRSBlXHDrAha2AKcsOJr8vzHCcJNU98520xCDrTNHzwZeCQbXSCpLKR0BIUJgpeGrFNEJZfZgkmhFoSDft2DeXmG8FCZui6sOeSvk/ni88ya27eza22Aiudu1FBmmenfIp5bs69XUCucj+ZWSqvUlrnMvjwWk9+tcR0DqPGfqMMTh0fiG29roSyDBIG9NPA+4aezyeXtxcL+slhM7fkNSG5b0y/WjTUbXduXo8UIeGkxS4hWR727sJF2U8Qu/S0GpcrlnbTV0kSSV4CuyqFqpqoAL0fcpno0kPPsDguQqAqZ1LD1UnSaZm6sorGVZkYWYwEroJSIVSXa5mQuJhlIYaN6ipKlAjFznv0UY/p2DIzJS5yWuy9OYaMFke2lxH2Yh/1Nc43ZI+AVRxUgNr3gGXYvHT4fqkZVl3Ngqc6qBpIOu5isA39rVMZcFAc8FkLUVyEahfdPAKIB22ZH06ALCwLoEexutiYBNvtEQigjqyTajuoawBQEfZ5vcBESJnDMFzhKosATB1UKxPpfzS4SHz/BEjzp0njdWKd+2WbZxaE+MeFH9O58Ysf5D2veVbMQ2VmrL5NUZXygysHMR2xeA6YJL8/sT45OeY19ctoxjrIa+02eeduq0B4YyjdqOd4cbauqvOK6vGy/yxSS2ui3WlcuZDToX0N0qHxpiylvQvCf4DIuWMv3hDJIXmnB9dL1yAvlLrhPcZFtdYfA5Vl5KzDfQ+WQiz26vMQsm0u24cLeK3PKWx9u1SIFExHLgiHvVsTHHE52LCXu8sRQCVRRpnXl5DYvP7KKO2sDiu561tbJD7S2daGKl4LBLlnwDEHdliVsuYmxx2zVzsDk7JCsDk3z9Az3Vt1ufTtjH37lxsY+FKIBL24v4PM0q7h5WQPuiD1bpJtZL4I/Jh5p6qDw6ktu7z+la0CJir0bPnhR7AKIWMY5VCjMN6Y3JGI/kboV63i9Mf3v9kdaqoFTUHapy7FfiyhPHbZ3qyAvzWhtSKXxkveK0i1InD3jLczW0eFRoZO51XnCCWyBAU1Tvan0IEb+5pvoNZTvVjia/Qnu6JNNE7P7qNC7FD0Y8FEbChbGqEtquhqCY5yEHrmjfmwU2/ew3jflw4AGMCMWqUkh7YG1m3lz9K7SGZX9TJHyzCaTRbV7WfaEzA3UXmQ/Ex3wLRXx9m53h5Uvst05zzav6x8qUY7JAI9i6VIHfPjj4CCITBXJgexg3/KtKHNWwL0WuPNUY7iA9N/YFxLLVr8fMO6gUfB41sPHuLc0TdxZ9c9Oz/aFx11cfwaSu+jERZboBsOngEp1OQqGWj3iRuUS+K9dAIvL338EMJ3fji+vLTttASsLuZjcTCdjS/abJfz0Oxx++r0C+sAHTrw4jf1vJYpIQMQWFH0QMsjDEffj+p5+Xhaz7q7sjQVKblvh9HEO/EF+m8WV/Vx2e7ry1n7v+KzXOzbNFTkhZ+7Jvt5aw/BnjlpDInz0VPjoqfRRrjN+IKKArbopmJdoPEg4z3+LOczR9EGWHYqbc/E/D+o9+Q1vFmT/GFfeJ8fIF4jAw/h3XkiIYq4va7nibSm/scqvA+yYRHHUTJiX38WU431Bb696o1h27HTyUv+nclX60+vSt69M6wP61rXpjqvTlHddpOeKyXpZoJS629NdN6j/B7Yyvsg='
TEMP_FILES = (
    "scripts/apply_release_owned_runtime_repair.py",
    ".github/workflows/apply-release-owned-runtime-repair.yml",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    drift = {
        relative: digest(ROOT / relative) if (ROOT / relative).is_file() else "missing"
        for relative, expected in EXPECTED.items()
        if not (ROOT / relative).is_file() or digest(ROOT / relative) != expected
    }
    if drift:
        raise RuntimeError(f"bounded repair source drifted: {drift}")

    with tempfile.NamedTemporaryFile(suffix=".patch") as handle:
        handle.write(zlib.decompress(base64.b64decode(PATCH_B64)))
        handle.flush()
        subprocess.run(
            ["git", "apply", "--whitespace=error", handle.name],
            cwd=ROOT,
            check=True,
        )

    for relative in TEMP_FILES:
        path = ROOT / relative
        if path.exists():
            path.unlink()

    manifest_path = ROOT / "PHASE_CANDIDATE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["phase"] = "B17i"
    manifest["root_name"] = "customer_agent_workspace_v20_17_b17i_production_execution_handoff_phase_candidate_env_blocked_20260731"
    manifest["required_environment"] = [
        str(value).replace("B17j", "B17i")
        for value in manifest.get("required_environment", [])
    ]
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    ignored = {"PHASE_CANDIDATE_MANIFEST.json", *TEMP_FILES}
    rows = []
    for relative in sorted(set(listed)):
        if not relative or relative in ignored:
            continue
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        rows.append({
            "path": relative,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    manifest["file_count"] = len(rows)
    manifest["files"] = rows
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    python = ROOT / "services/agent-service/.venv/bin/python"
    executable = str(python) if python.is_file() else "python"
    subprocess.run(
        [executable, "-m", "compileall", "-q", str(ROOT / "scripts")],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()

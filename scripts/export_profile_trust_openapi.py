"""현재 FastAPI 모델에서 백엔드 공유용 프로필 신뢰 OpenAPI 계약을 내보낸다.

같은 서버의 persona·practice·simulation 변경이 이 계약 파일을 흔들지 않도록
/ai/api/v1/profile-trust 경로와 그 경로가 참조하는 스키마만 남긴다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app  # noqa: E402

PATH_PREFIX = "/ai/api/v1/profile-trust"
OUTPUT = PROJECT_ROOT / "docs/v2docs/openapi.json"
REF_PREFIX = "#/components/schemas/"


def _refs(node: object) -> set[str]:
    if isinstance(node, dict):
        found = {node["$ref"].removeprefix(REF_PREFIX)} if "$ref" in node else set()
        for value in node.values():
            found |= _refs(value)
        return found
    if isinstance(node, list):
        return set().union(*(_refs(value) for value in node))
    return set()


def profile_trust_openapi() -> dict:
    schema = app.openapi()
    paths = {path: item for path, item in schema["paths"].items() if path.startswith(PATH_PREFIX)}
    all_schemas = schema.get("components", {}).get("schemas", {})

    needed: set[str] = set()
    pending = _refs(paths)
    while pending:
        name = pending.pop()
        if name not in needed:
            needed.add(name)
            pending |= _refs(all_schemas[name])

    tag_names = {tag for item in paths.values() for operation in item.values() for tag in operation.get("tags", [])}
    return {
        "openapi": schema["openapi"],
        "info": {**schema["info"], "title": "별이삼샵 Profile Trust AI API"},
        "tags": [tag for tag in schema.get("tags", []) if tag["name"] in tag_names],
        "paths": paths,
        "components": {"schemas": {name: all_schemas[name] for name in sorted(needed)}},
    }


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(profile_trust_openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT.relative_to(PROJECT_ROOT))


if __name__ == "__main__":
    main()

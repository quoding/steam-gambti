"""staging 스냅샷 검증·승격 / 롤백 / 상태 확인.

uv run python -m gembti.collector.promote status
uv run python -m gembti.collector.promote promote var/staging/<id>.db
uv run python -m gembti.collector.promote rollback <snapshot_id>
"""
import argparse
import json
from pathlib import Path

from gembti import catalog, config


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("promote")
    p.add_argument("path")
    r = sub.add_parser("rollback")
    r.add_argument("snapshot_id")
    args = ap.parse_args()
    if args.cmd == "status":
        print(json.dumps(catalog.read_active_pointer(), ensure_ascii=False, indent=1))
        for f in sorted(config.SNAPSHOT_DIR.glob("*.db")):
            print(f.name, catalog.validate_snapshot(f) or "OK")
    elif args.cmd == "promote":
        from gembti.collector.build_catalog import CATALOG_POLICY, verify_snapshot
        checks = verify_snapshot(Path(args.path))
        for k, v in checks.items():
            print(f"  [{'OK' if v else 'FAIL'}] {k}")
        if not all(checks.values()):
            raise SystemExit(f"카탈로그 정책 {CATALOG_POLICY['catalog_policy_version']} 검증 실패: 승격하지 않음")
        print(json.dumps(catalog.promote(Path(args.path)), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(catalog.rollback(args.snapshot_id), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

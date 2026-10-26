"""경로·환경 설정. 비밀값은 환경변수로만 받는다."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """프로젝트 루트 .env(KEY=VALUE)를 읽는다. 이미 있는 환경변수는 덮어쓰지 않는다. 값은 출력하지 않는다."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.removeprefix("export ").split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k, v)


load_env_file()
POLICY_DIR = Path(os.environ.get("GEMBTI_POLICY_DIR", REPO_ROOT / "policy"))
CATALOG_DIR = REPO_ROOT / "catalog"
VAR_DIR = Path(os.environ.get("GEMBTI_VAR_DIR", REPO_ROOT / "var")).resolve()

SERVICE_DB = Path(os.environ.get("GEMBTI_SERVICE_DB", VAR_DIR / "service.db")).resolve()
SNAPSHOT_DIR = VAR_DIR / "snapshots"
ACTIVE_SNAPSHOT_FILE = VAR_DIR / "active_snapshot.json"
RAW_DIR = VAR_DIR / "raw"
STAGING_DIR = VAR_DIR / "staging"
BACKUP_DIR = VAR_DIR / "backups"

# 자유 서술 보관 기간(일), 길이 제한
FREE_TEXT_RETENTION_DAYS = 30
FREE_TEXT_MAX_CHARS = 300
FEEDBACK_MAX_CHARS = 500
MAX_PLAYED_APPIDS = 50
MAX_BODY_BYTES = 32 * 1024
RATE_LIMIT_PER_MINUTE = int(os.environ.get("GEMBTI_RATE_LIMIT_PER_MINUTE", "30"))

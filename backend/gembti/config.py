"""경로·환경 설정. 비밀값은 환경변수로만 받는다."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
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

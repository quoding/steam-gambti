# 겜BTI (steam-gambti)

상황 질문 20개에 답하면 실제 Steam 태그(tagid) 20개에 대한 취향을 **고정 배점**으로 판정하고,
유형 카드 15개(또는 ‘아직 발견 중’)와 검수한 Steam 게임 300개 중 최대 5개를 추천하는 한국어 서비스.

- 판정은 서버에서 결정적으로 계산한다. 같은 정책·같은 객관식 답변은 언제나 같은 판정용 `session_id`와 유형을 만든다.
- 근거가 부족하면 억지로 유형을 붙이지 않고 ‘아직 발견 중’으로 보여준다.
- 추천은 ‘답변에 맞춘 추천’과 ‘참고 추천’을 구분한다. 가격·계정 연동·공유 링크는 다루지 않는다.

## 구조

| 경로 | 내용 |
|---|---|
| `policy/` | 검수 가능한 정책 데이터: 태그·문항·배점·그룹·유형·별칭·카탈로그 할당·콘텐츠 검토·버전 |
| `backend/gembti/engine.py` | 고정 배점·상태(positive/avoid/unknown 등)·결정적 session_id·동점 처리·유형 카드 |
| `backend/gembti/recommend.py` | 개인화/참고 추천, 강한 기피 제외·약한 기피 패널티, 인기 기준선 |
| `backend/gembti/app.py`, `service.py`, `db.py` | FastAPI API · SQLite 저장 |
| `backend/gembti/catalog.py` | 카탈로그 스냅샷 검증·승격·롤백 |
| `backend/gembti/collector/` | 오프라인 Steam 수집(GetTagList·GetItems)과 300개 선정(정수계획) |
| `backend/gembti/evaluation.py`, `eval_admin.py` | 블라인드 평가(고정 배점 vs 인기 기준선)·집계 |
| `backend/gembti/maintenance.py` | 서술을 뺀 백업·복원·만료 서술 정리 |
| `experiments/jev/` | 오프라인 재판정 실험 도구(서비스와 분리) |
| `catalog/` | seed 후보와 출처, 동결 manifest·검수 보고(Steam 공개 데이터) |
| `web/` | Next.js 정적 웹(기본 basePath `/gembti`) |

## 실행

```bash
# 백엔드 (backend/)
uv sync --group dev --group collector
uv run pytest -q
uv run mypy gembti --ignore-missing-imports
uv run ruff check gembti tests
uv run uvicorn gembti.app:app --host 127.0.0.1 --port 8790

# 웹 (web/)
npm ci
npx tsc --noEmit
npm run dev                                   # :3790, /gembti/api → 로컬 API 프록시 (localhost로 접속)
NEXT_PUBLIC_BASE_PATH=/gembti npm run build   # 정적 빌드 → web/out

# 카탈로그 (backend/, 오프라인 작업 · 서비스 요청 경로에서는 호출하지 않음)
uv run python -m gembti.collector.harvest_seeds --per-tag 300
uv run python -m gembti.collector.fetch_items --run-id <RUN> --tags
uv run python -m gembti.collector.build_catalog --run-id <RUN>
uv run python -m gembti.collector.promote promote ../var/staging/<snapshot>.db
```

데이터(DB·스냅샷·백업·수집 원본·실험 결과)는 `var/`에 저장되며 저장소에 포함되지 않는다.
설정은 환경변수(`GEMBTI_VAR_DIR` 등)로 바꾼다.

## 원칙
- 검사·결과 요청은 저장된 DB만 사용하고 Steam·외부 모델을 호출하지 않는다.
- 자유 서술은 판정·추천에 쓰지 않으며 30일 뒤 삭제되고 백업에 넣지 않는다.
- 테스트용 가상 게임은 `backend/tests/`에만 있고 서비스 카탈로그와 섞이지 않는다.
- 게임 정보 출처: Steam. 이 프로젝트는 Valve와 관련이 없다.

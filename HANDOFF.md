# 인수인계 메모 (HANDOFF)

새 대화/세션에서 이어서 작업할 때 이 파일부터 읽는다. 코드 사용법은 `README.md`, 이 파일은 **대화에서 정해진 맥락과 남은 일**.

- 저장소: `2020oporto-collab/atmosphere`, 작업 브랜치 `claude/atmospheric-pressure-hsoh-thickness-c4mgyn`
- 핵심 파일: `analyze.py` 하나 (조회 → 연결 → 분석 → 엑셀/PNG/HTML 리포트)
- 마지막 갱신: 2026-10-16 (조회 캐시/동시 조회 추가)

## 1. 목표
대기압 변화에 따라 HSOH(SOH) 두께가 어떻게 변하는지 본다.
결과 지표: 대기압 1 hPa 당 두께 변화(Å/hPa), 설비별 민감도, 대기압 구간별 두께, 시차 분석.

## 2. 사용자 환경 (중요)
- **회사 PC 에서 GitHub 를 쓸 수 없다.** 코드는 채팅에 준 코드를 VS Code 에 **복사·붙여넣기** 해서 옮긴다.
  - 그래서 변경을 줄 때는 "어디부터 어디까지 교체" 를 분명히 하고, 사용자가 고친 설정 부분(맨 위 SQL·COLUMNS 등)은 덮어쓰지 않게 한다.
  - 붙여넣기 중 보이지 않는 문자가 사라질 수 있으니 BOM 같은 특수문자는 `﻿` 처럼 이스케이프로 쓴다.
- 개발 환경: Windows + VS Code (Python). 주피터 셀에 붙여넣어도 동작하게 만들어 둠.
- 데이터 조회: 사내 라이브러리 `from bigdataquery import *` → `getData(SQL)` 이 pandas DataFrame 반환 (Impala SQL, 로그인 불필요).
  - 이 세션(클라우드)에서는 사내 DB 에 접속할 수 없다. 테스트는 가짜 `bigdataquery` 모듈(sqlite)과 가짜 CSV 로 한다.
- 사내 pip 설치가 막혀 있을 수 있다. 필수: pandas, numpy, scipy, matplotlib, openpyxl. 선택: xlsxwriter(있으면 엑셀 저장이 빠름; 없으면 Pylance 경고만 뜸 → `# type: ignore` 처리함).
- 인터넷이 없어도 열리도록 HTML 리포트는 외부 라이브러리/CDN 없이 자체 SVG+canvas 로 그린다.

## 3. 데이터 구조 (쿼리 3개)
| 쿼리 | 내용 | 코드 설정명 | 주요 컬럼 (COLUMNS 기본값) |
|---|---|---|---|
| 1 | 대기압 (5분 간격, hPa) | `SQL_PRESSURE` / `COLUMNS["pressure"]` | `act_time`, `value` |
| 2 | LOT-계측 | `SQL_MEAS` / `COLUMNS["meas"]` | `LOT_ID`, `WAFER_ID`, `ITEM_ID`(=AVG), `FAB_VALUE`(Å), `MEAS_TIME` |
| 3 | LOT-공정 진행 | `SQL_PROC` / `COLUMNS["proc"]` | `LOT_ID`, `EQP_ID`, `TKIN_TIME` (+선택 `wafer`) |

- 연결: 쿼리2 + 쿼리3 은 **LOT**(쿼리3 에 WAFER 컬럼이 있으면 LOT+WAFER) 로 연결.
  한 LOT 에 공정 이력이 여러 건이면 **계측 시간 이전의 가장 늦은 TKIN_TIME** 사용.
- 대기압 매칭: **TKIN_TIME(공정 진행 시간)** 에 가장 가까운 대기압 (±5분). 계측 시간이 아님 — 두께는 공정 시점에 결정되므로.
- 시간 형식: `2026-09-14 오후 4:14:59` (오전/오후) 또는 24시간 형식 둘 다 처리.
- 컬럼명 비교는 대소문자·공백·`_`·`-` 무시 (`LOT ID` = `lot_id`). 사용자의 실제 컬럼은 `lot_id`, `wafer_id` 등 소문자/언더스코어 형태였음.
- 쿼리3 은 사용자 결정으로 **기간 조건**으로 조회 (`{start}` = 계측 시작일 − `PROC_LOOKBACK_DAYS`(3일)). `{lots}` 자리표시자도 지원(선택).
- 쿼리1 의 기간은 쿼리3(계측 LOT 에 해당하는 것만) 의 TKIN_TIME 범위에 맞춰 자동으로 정해짐.
- 조회 순서: 쿼리2 → 쿼리3 → 쿼리1. 조회 원본은 `output/raw_*.csv` 로 저장.

## 4. 출력물
- `output/pressure_vs_soh_report.html` — 인터랙티브 리포트 (사용자가 주로 보는 것)
  - 시계열: **기본 = 한 그래프에 왼쪽 축 두께 / 오른쪽 축 대기압** (사용자 요청). ⚙ 에서 "위아래 분리" 선택 가능
  - 모든 차트 `⚙ 축 설정`: 축 최소/최대 직접 입력 (비우면 자동)
  - 시계열 범례: 위치(위/아래/오른쪽/숨김), 항목 클릭으로 대기압·설비 숨기기
  - 설비 켜기/끄기, 시계열 드래그로 기간 선택 → 전체 차트/표 재계산, 호버 툴팁(LOT/WAFER/두께/대기압)
  - 점은 canvas 로 그림 (웨이퍼 수만 장에서도 빠름)
- `output/pressure_vs_soh_report.xlsx` — 요약_회귀(p-value 포함) / 대기압구간별 / 시차분석 / 매칭데이터 / 매칭실패(사유) / 설정 / 그래프
  - 매칭데이터가 `EXCEL_MAX_ROWS`(20,000) 초과면 `output/matched_data.csv` 로 따로 저장
- `output/1~5_*.png` — 정적 그래프 (`MAKE_PNG = False` 로 생략 가능)

## 5. 진행 이력 요약
1. CSV 2개(대기압, SOH) 기반 분석 → 2. Impala(impyla) 직접 조회 시도 → 사용자 환경이 `bigdataquery.getData` 라서 교체
3. 쿼리 3개 구조로 변경 (계측 / 공정 분리) → 4. HTML 인터랙티브 리포트 추가 → 축 설정·범례·이중축 겹치기
5. 1개월 데이터 속도 개선: 엑셀 셀 서식 루프 제거(가장 큰 병목, 46초→6초), canvas 점, 단계별 소요 시간 출력

## 6. 남은 일 / 확인 대기
- [x] 실데이터 1개월 측정 결과 (2026-10-16): 조회 1048.6초(쿼리1 빠름, **쿼리2 약 7분, 쿼리3 약 9분**), 연결·매칭 0초, 분석 4.7초, 저장 2.1초
  - 대응 완료: 쿼리별 소요 시간 표시, 날짜별 캐시(`cache/`, `USE_CACHE`, `CHUNK_DAYS`), 쿼리2·3 동시 조회(`PARALLEL_QUERIES`)
- [ ] 동시 조회·캐시 적용 후 실데이터 소요 시간 확인 (bigdataquery 가 동시 호출을 지원하는지도 확인; 오류 시 `PARALLEL_QUERIES = False`)
- [ ] 쿼리2·3 SQL 자체 개선 (가장 효과 큼): 날짜 **파티션 컬럼 조건**, 쿼리2 **HSOH 계측 STEP/ITEM 조건**, 쿼리3 **HSOH STEP 조건**,
  `select *` 대신 필요한 컬럼만, `PROC_LOOKBACK_DAYS` 줄이기. 사용자의 실제 SQL(테이블명 가림)을 받아서 같이 보기
- [ ] 사용자가 xlsxwriter 설치 가능한지 (선택 사항)
- [ ] 3~6개월 분석 시 웨이퍼 수가 매우 많아지면 HTML 파일 크기/반응 확인
- 사용자가 아직 확인 안 한 것: 실제 데이터에서 Å/hPa 결과가 타당한지, 설비 수(8대 초과 시 9번째부터 회색)

## 7. 작업 방식 메모
- 사용자는 한국어로 대화. "코드 작성해" 등 명시적 요청 전에는 계획/설명만 하길 원했던 적이 있음 (초기). 이후로는 요청 시 바로 구현.
- 변경 후에는 가짜 데이터로 실행 + Chromium(Playwright, `/opt/pw-browsers/chromium`)으로 HTML 렌더/에러 확인 후 커밋.
- 가짜 데이터 생성: `python tools/make_sample_data.py` (재작업 LOT, 계측 후 재진행 LOT, 공정정보 없는 LOT 포함).
- 테스트 실행: `python analyze.py --source csv`

# 대기압 vs HSOH 두께 분석

공정 투입 시간(`TKIN_TIME`)의 대기압을 웨이퍼별 SOH 두께와 매칭해서,
대기압이 변할 때 두께가 어떻게 변하는지(Å/hPa) 분석합니다.
`analyze.py` 파일 하나로 동작합니다.

## 데이터 연결 구조

| 쿼리 | 내용 | 주요 컬럼 |
|---|---|---|
| 쿼리1 | 대기압 | 시간, 대기압 |
| 쿼리2 | LOT - 계측 | LOT, WAFER, ITEM(AVG), 두께, 계측시간 |
| 쿼리3 | LOT - 공정 진행 | LOT, 설비, TKIN_TIME (+WAFER) |

1. 쿼리2 + 쿼리3 을 LOT(쿼리3 에 WAFER 가 있으면 LOT+WAFER)으로 연결 → 웨이퍼별 설비, TKIN_TIME
   - 한 LOT 에 공정 이력이 여러 건이면 **계측 시간 이전의 가장 늦은 TKIN_TIME** 사용
2. TKIN_TIME 에 가장 가까운 쿼리1 대기압을 붙임 (±5분)
3. 연결 실패는 `매칭실패` 시트에 사유(공정정보 없음 / 대기압 없음)와 함께 기록

## 사용법 (bigdataquery 조회)

1. `analyze.py` 맨 위 "설정" 을 채운다.
   - `DATE_FROM`, `DATE_TO` : 분석 기간 (계측 기준, 둘 다 포함)
   - `SQL_PRESSURE`, `SQL_MEAS`, `SQL_PROC` : 평소 `getData()` 에 넣던 SQL
     - `{start}` `{end}` : 기간으로 자동 치환
     - `{lots}` : 쿼리2 결과의 LOT 목록으로 치환 (쿼리3 에서 `LOT_ID in ({lots})`)
     - 쿼리3 에는 HSOH 공정 STEP 조건을 꼭 넣을 것
     - 쿼리1 의 기간은 쿼리3 의 TKIN_TIME 범위에 맞춰 자동으로 정해진다
   - `COLUMNS` : 각 쿼리 결과의 실제 컬럼명 (결과에 컬럼이 많아도 여기 지정한 것만 사용)
2. 실행: VS Code 에서 F5 또는 `python analyze.py`
   - 평소 `from bigdataquery import *` 가 되는 파이썬 환경에서 실행해야 한다.
   - 기간만 바꿔 실행: `python analyze.py --from 2026-09-01 --to 2026-09-30`
3. 결과: `output/`
   - `pressure_vs_soh_report.xlsx` : 그래프 / 요약_회귀 / 대기압구간별 / 시차분석 / 매칭데이터 / 매칭실패 / 설정
   - `1_timeseries.png` ~ `5_lag.png`
   - `raw_pressure.csv`, `raw_meas.csv`, `raw_proc.csv` : 조회 원본

## CSV 로 분석

`DATA_SOURCE = "csv"` 로 바꾸거나 `python analyze.py --source csv`.
`input/pressure/`, `input/meas/`, `input/proc/` 안의 CSV 를 모두 읽는다 (UTF-8 / CP949 자동 인식).
`output/raw_*.csv` 를 여기로 옮기면 다시 조회하지 않고 재분석할 수 있다.

## 분석 내용

- **매칭**: `TKIN_TIME` 에 가장 가까운 대기압 (±5분 이내, 없으면 `매칭실패` 시트로)
  + 직전 30분 평균 대기압, 30분간 대기압 변화량
- **회귀**: 전체 / 설비 평균 제거 / 설비별 기울기(Å/hPa), R², p-value, Pearson·Spearman 상관
  - 설비마다 두께 기본값이 달라서, 전체 분석은 "설비 평균 제거" 결과를 보는 것이 정확하다.
- **대기압 구간별**: 1 hPa 구간마다 두께 평균·σ·웨이퍼수 (전체 + 설비별)
- **시차 분석**: TKIN_TIME 보다 0~240분 앞선 대기압과의 상관

## 가짜 데이터 다시 만들기

`python tools/make_sample_data.py`

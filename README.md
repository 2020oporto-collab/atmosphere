# 대기압 vs HSOH 두께 분석

공정 투입 시간(`TKIN_TIME`)에 측정된 대기압을 웨이퍼별 SOH 두께와 매칭해서,
대기압이 변할 때 두께가 어떻게 변하는지(Å/hPa) 분석합니다.

## 사용법

1. CSV 넣기 (폴더 안 CSV 는 여러 개여도 모두 합쳐서 읽음)
   - `input/pressure/` : 대기압 CSV
   - `input/soh/`      : SOH 두께 CSV
   - 들어있는 `*_sample.csv` 는 가짜 데이터이므로 실제 데이터를 넣을 때 삭제하세요.
2. 실행
   - Windows: `run.bat` 더블클릭 (처음 한 번 필요한 패키지를 설치)
   - 또는 `pip install -r requirements.txt` 후 `python analyze.py`
   - 파일 직접 지정: `python analyze.py --pressure 대기압.csv --soh soh.csv --out 결과폴더`
   - Impala 에서 바로 조회: 아래 "Impala 조회" 참고
3. 결과: `output/`
   - `pressure_vs_soh_report.xlsx` : 그래프 / 요약_회귀 / 대기압구간별 / 시차분석 / 매칭데이터 / 매칭실패 / 설정
   - `1_timeseries.png` ~ `5_lag.png`

## 입력 형식

CSV 에 다른 컬럼이 많아도 아래 컬럼만 추출합니다. 컬럼명 앞뒤 공백·대소문자는 무시하고,
인코딩은 UTF-8 / CP949 를 자동 인식합니다. 컬럼명이 다르면 `analyze.py` 상단 `COLUMNS` 를 고치세요.

| 파일 | 컬럼 | 내용 |
|---|---|---|
| 대기압 | `act_time` | `2026-09-14 오후 4:14:59` 형식 (24시간 형식도 가능) |
| 대기압 | `value` | 대기압 (hPa) |
| SOH | `LOT ID`, `WAFER ID`, `EQP_ID` | |
| SOH | `TKIN_TIME` | 대기압 매칭 기준 시간 (같은 형식) |
| SOH | `ITEM_ID` | `AVG` 행만 사용 |
| SOH | `FAB_VALUE` | 두께 (Å) |

## 분석 내용

- **매칭**: `TKIN_TIME` 에 가장 가까운 대기압 (±5분 이내, 없으면 `매칭실패` 시트로)
  + 직전 30분 평균 대기압, 30분간 대기압 변화량
- **회귀**: 전체 / 설비 평균 제거 / 설비별 기울기(Å/hPa), R², p-value, Pearson·Spearman 상관
  - 설비마다 두께 기본값이 달라서, 전체 분석은 "설비 평균 제거" 결과를 보는 것이 정확합니다.
- **대기압 구간별**: 1 hPa 구간마다 두께 평균·σ·웨이퍼수 (전체 + 설비별)
- **시차 분석**: TKIN_TIME 보다 0~240분 앞선 대기압과의 상관 — 상관이 가장 높은 시차 확인

허용 오차, 구간 폭, 시차 범위 등은 `analyze.py` 상단 설정에서 바꿀 수 있습니다.

## Impala 조회 (CSV 대신 DB 에서 바로)

1. `queries/pressure.sql`, `queries/soh.sql` 의 `<...>` 를 실제 DB/테이블/컬럼명으로 바꾼다.
   - 결과 컬럼명은 `AS` 로 위 입력 형식 컬럼명에 맞춘다 (다른 컬럼이 더 있어도 됨).
   - 기간 조건 `{start}` `{end}` 는 실행 시 자동으로 채워진다. 날짜 파티션 컬럼이 있으면
     `{start_ymd}` `{end_ymd}` 조건 주석을 풀어서 쓰면 조회가 빨라진다.
2. `db_config.ini` 에 Impala `host` / `port` 입력 (로그인 없음 = `auth_mechanism = NOSASL`).
3. 실행
   - Windows: `run_db.bat` 더블클릭 → 시작일/종료일 입력
   - 또는 `pip install impyla` 후 `python analyze.py --from 2026-09-01 --to 2026-09-30`
4. 조회 원본은 `output/raw_pressure.csv`, `output/raw_soh.csv` 로 저장된다.
   이 파일을 `input/` 에 넣으면 다시 조회하지 않고 CSV 모드로 재분석할 수 있다.

접속이 안 되면: host/port 확인 → `auth_mechanism = PLAIN` 으로 변경 → 담당자에게 문의.

## 가짜 데이터 다시 만들기

`python tools/make_sample_data.py`

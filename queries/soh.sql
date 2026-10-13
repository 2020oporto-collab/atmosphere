-- SOH 두께 조회 (Impala SQL)
-- <...> 부분을 실제 DB/테이블/컬럼명으로 바꾸세요.
-- 결과 컬럼명은 반드시 아래 AS 이름으로 맞춰주세요.
-- 기간은 실행할 때 --from / --to 로 자동으로 들어갑니다:
--   {start}, {end}         : '2026-09-01 00:00:00' 형식 (end 는 미포함)
--   {start_ymd}, {end_ymd} : '20260901' 형식 (날짜 파티션 컬럼용, 둘 다 포함)
SELECT
    <LOT컬럼>    AS `LOT ID`,
    <WAFER컬럼>  AS `WAFER ID`,
    <설비컬럼>   AS EQP_ID,
    <TKIN컬럼>   AS TKIN_TIME,
    <ITEM컬럼>   AS ITEM_ID,
    <측정값컬럼> AS FAB_VALUE
FROM <DB명>.<계측테이블>
WHERE <ITEM컬럼> = 'AVG'
  AND <TKIN컬럼> >= '{start}'
  AND <TKIN컬럼> <  '{end}'
  -- AND <파티션컬럼> BETWEEN '{start_ymd}' AND '{end_ymd}'
  -- AND <STEP컬럼> = '<HSOH 공정 STEP>'
ORDER BY <TKIN컬럼>

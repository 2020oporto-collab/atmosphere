-- 대기압 조회 (Impala SQL)
-- <...> 부분을 실제 DB/테이블/컬럼명으로 바꾸세요.
-- 결과 컬럼명은 반드시 AS act_time, AS value 로 맞춰주세요.
-- 기간은 실행할 때 --from / --to 로 자동으로 들어갑니다:
--   {start}, {end}         : '2026-09-01 00:00:00' 형식 (end 는 미포함)
--   {start_ymd}, {end_ymd} : '20260901' 형식 (날짜 파티션 컬럼용, 둘 다 포함)
SELECT
    <시간컬럼>   AS act_time,
    <대기압컬럼> AS value
FROM <DB명>.<대기압테이블>
WHERE <시간컬럼> >= '{start}'
  AND <시간컬럼> <  '{end}'
  -- AND <파티션컬럼> BETWEEN '{start_ymd}' AND '{end_ymd}'
  -- AND <파라미터명컬럼> = '<대기압 항목명>'
ORDER BY <시간컬럼>

"""가짜(테스트용) CSV 데이터 생성기.

쿼리1(대기압), 쿼리2(계측), 쿼리3(공정 진행) 결과를 흉내 낸 CSV 3개를 만든다.
실제 데이터처럼 분석에 필요 없는 컬럼도 섞는다.
    python tools/make_sample_data.py
"""
import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
random.seed(42)

START = datetime(2026, 9, 14, 0, 4, 59)
END = datetime(2026, 9, 17, 0, 0, 0)
EQP_OFFSET = {"COT01": 0.0, "COT02": 15.0, "COT03": -10.0}
EQP_SENS = {"COT01": 6.0, "COT02": 4.0, "COT03": 8.0}  # Å/hPa (가짜 민감도)


def fmt(t):
    ap = "오전" if t.hour < 12 else "오후"
    h = t.hour % 12 or 12
    return f"{t:%Y-%m-%d} {ap} {h}:{t:%M:%S}"


def true_pressure(t):
    m = (t - START).total_seconds() / 60
    return 1012.0 + 3.0 * math.sin(m / 900) + 1.0 * math.sin(m / 170)


def write(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    rows = []
    t = START
    while t <= END:
        rows.append(["FAB1", "ATM_PRESS", fmt(t), round(true_pressure(t) + random.gauss(0, 0.08), 2),
                     "hPa", "OK"])
        t += timedelta(minutes=5)
    write(ROOT / "input" / "pressure" / "pressure_sample.csv",
          ["SITE", "PARAM_NAME", "act_time", " value", "UNIT", "STATUS"], rows)

    meas, proc = [], []
    t = START + timedelta(hours=1)
    lot_no = 0
    while t < END - timedelta(hours=3):
        lot_no += 1
        lot = f"LOT{2609000 + lot_no}"
        eqp = list(EQP_OFFSET)[lot_no % 3]
        tk = t + timedelta(seconds=random.randint(0, 59))
        if lot_no == 5:  # 재작업: 먼저 한 번 진행된 이력 (계측 전, 더 이른 시간) -> 늦은 이력이 쓰여야 함
            proc.append([lot, "PH_HSOH", "COT01", "SOH_RCP_A", fmt(tk - timedelta(hours=1)), "N"])
        if lot_no != 7:  # LOT 7 은 공정 정보 없음 -> 매칭실패로 가야 함
            proc.append([lot, "PH_HSOH", eqp, "SOH_RCP_A", fmt(tk), "N"])
        if lot_no == 9:  # 계측 이후 다시 진행된 이력 -> 무시되어야 함
            proc.append([lot, "PH_HSOH", "COT02", "SOH_RCP_A", fmt(tk + timedelta(hours=10)), "Y"])
        p = true_pressure(tk)
        for wf in range(1, 26, 3):
            avg = 2000 + EQP_OFFSET[eqp] + (p - 1012) * EQP_SENS[eqp] + random.gauss(0, 4)
            mt = tk + timedelta(minutes=90 + wf * 2)
            for item, val in [("AVG", avg), ("MAX", avg + random.uniform(10, 20)),
                              ("MIN", avg - random.uniform(10, 20))]:
                meas.append([lot, f"{lot}.{wf:02d}", "MEAS_SOH", "THK01", item, round(val, 1),
                             fmt(mt)])
        t += timedelta(minutes=random.randint(40, 120))
    write(ROOT / "input" / "meas" / "meas_sample.csv",
          ["LOT_ID", "WAFER_ID", "STEP_ID", "MEAS_EQP_ID", "ITEM_ID", "FAB_VALUE", "MEAS_TIME"],
          meas)
    write(ROOT / "input" / "proc" / "proc_sample.csv",
          ["LOT_ID", "STEP_ID", "EQP_ID", "RECIPE_ID", "TKIN_TIME", "REWORK_FLAG"], proc)
    print(f"대기압 {len(rows)}행, 계측 {len(meas)}행, 공정 {len(proc)}행 생성")


if __name__ == "__main__":
    main()

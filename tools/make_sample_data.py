"""가짜(테스트용) CSV 데이터 생성기.

실제 데이터처럼 분석에 필요 없는 컬럼도 섞어서 만든다.
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


def main():
    pressure = {}
    rows = []
    t = START
    while t <= END:
        p = round(true_pressure(t) + random.gauss(0, 0.08), 2)
        pressure[t] = p
        rows.append(["FAB1", "ATM_PRESS", fmt(t), p, "hPa", "OK"])
        t += timedelta(minutes=5)
    out = ROOT / "input" / "pressure" / "pressure_sample.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["SITE", "PARAM_NAME", "act_time", " value", "UNIT", "STATUS"])
        w.writerows(rows)

    soh = []
    t = START + timedelta(hours=1)
    lot_no = 0
    while t < END - timedelta(hours=2):
        lot_no += 1
        lot = f"LOT{2609000 + lot_no}"
        eqp = list(EQP_OFFSET)[lot_no % 3]
        for wf in range(1, 26, 3):
            tk = t + timedelta(minutes=wf * 2, seconds=random.randint(0, 59))
            p = true_pressure(tk)
            avg = 2000 + EQP_OFFSET[eqp] + (p - 1012) * EQP_SENS[eqp] + random.gauss(0, 4)
            base = [lot, f"{lot}.{wf:02d}", "PH_SOH", eqp, "SOH_RCP_A", fmt(tk)]
            for item, val in [("AVG", avg), ("MAX", avg + random.uniform(10, 20)),
                              ("MIN", avg - random.uniform(10, 20))]:
                soh.append(base + [item, round(val, 1), "THK_MEAS01", fmt(tk + timedelta(hours=1))])
        t += timedelta(minutes=random.randint(40, 120))
    out = ROOT / "input" / "soh" / "soh_thickness_sample.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["LOT ID", "WAFER ID", "STEP_ID", "EQP_ID", "RECIPE_ID", " TKIN_TIME",
                    " ITEM_ID", " FAB_VALUE", "MEAS_EQP_ID", "MEAS_TIME"])
        w.writerows(soh)
    print(f"대기압 {len(rows)}행, SOH {len(soh)}행 생성")


if __name__ == "__main__":
    main()

"""대기압 vs HSOH 두께 분석.

사용법
    1) input/pressure/ 에 대기압 CSV, input/soh/ 에 SOH 두께 CSV 를 넣는다.
       (폴더 안의 CSV 는 여러 개여도 모두 합쳐서 읽는다)
    2) python analyze.py
    3) output/ 폴더에 엑셀 리포트와 그래프가 생성된다.

    파일/폴더를 직접 지정할 수도 있다.
        python analyze.py --pressure 대기압.csv --soh soh.csv --out 결과폴더

    Impala 에서 바로 조회 (CSV 대신):
        python analyze.py --from 2026-09-01 --to 2026-09-30
        - 접속 정보: db_config.ini, 쿼리: queries/pressure.sql, queries/soh.sql
        - 조회 결과는 output/raw_*.csv 로도 저장된다.

CSV 에 컬럼이 많아도 아래 COLUMNS 에 지정한 컬럼만 추출해서 사용한다.
컬럼명이 다르면 COLUMNS 만 고치면 된다.
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# 설정 (실제 데이터에 맞게 수정)
# ============================================================
COLUMNS = {
    "pressure": {
        "time": "act_time",      # 대기압 측정 시간
        "value": "value",        # 대기압 값 (hPa)
    },
    "soh": {
        "lot": "LOT ID",
        "wafer": "WAFER ID",
        "eqp": "EQP_ID",
        "time": "TKIN_TIME",     # 대기압과 매칭할 기준 시간
        "item": "ITEM_ID",
        "value": "FAB_VALUE",    # 두께 (Å)
    },
}
ITEM_FILTER = "AVG"              # ITEM_ID 가 이 값인 행만 사용 (None 이면 필터 안 함)
MATCH_TOLERANCE_MIN = 5          # TKIN_TIME 과 대기압 시간 차이 허용 범위(분)
WINDOW_MIN = 30                  # 직전 평균/변화량 계산 구간(분)
PRESSURE_BIN_HPA = 1.0           # 대기압 구간 폭(hPa)
LAG_MAX_MIN = 240                # 시차 분석 최대 범위(분)
LAG_STEP_MIN = 15                # 시차 분석 간격(분)
MIN_N_FOR_STATS = 3              # 통계 계산 최소 웨이퍼 수

ENCODINGS = ("utf-8-sig", "cp949", "euc-kr", "utf-8")
ROOT = Path(__file__).resolve().parent


# ============================================================
# 읽기
# ============================================================
def _norm(name):
    return re.sub(r"\s+", " ", str(name).replace("﻿", "")).strip().upper()


def pick_columns(df, wanted, src):
    """df 에서 wanted(내부이름->컬럼명) 컬럼만 골라 내부이름으로 바꿔 반환."""
    targets = {_norm(v): k for k, v in wanted.items()}
    df = df[[c for c in df.columns if _norm(c) in targets]]
    df = df.rename(columns=lambda c: targets[_norm(c)])
    df = df.loc[:, ~df.columns.duplicated()]
    missing = [wanted[k] for k in wanted if k not in df.columns]
    if missing:
        raise SystemExit(f"[오류] {src} 에 필요한 컬럼이 없습니다: {missing}\n"
                         f"       analyze.py 상단 COLUMNS 설정(또는 SQL 의 AS 이름)을 확인하세요.")
    return df[list(wanted)].astype("string").apply(lambda x: x.str.strip())


def read_csv_columns(path, wanted):
    """CSV 에서 wanted 컬럼만 읽는다. 인코딩은 자동 판별."""
    targets = {_norm(v) for v in wanted.values()}
    last_err = None
    for enc in ENCODINGS:
        try:
            df = pd.read_csv(path, encoding=enc, dtype=str,
                             usecols=lambda c: _norm(c) in targets)
            break
        except UnicodeDecodeError as e:
            last_err = e
    else:
        raise SystemExit(f"[오류] {path} 인코딩을 읽을 수 없습니다: {last_err}")
    return pick_columns(df, wanted, path)


def collect_csvs(path):
    path = Path(path)
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.suffix.lower() == ".csv")
    elif path.exists():
        files = [path]
    else:
        files = []
    if not files:
        raise SystemExit(f"[오류] CSV 파일이 없습니다: {path}")
    return files


def read_all(path, wanted):
    files = collect_csvs(path)
    frames = [read_csv_columns(f, wanted) for f in files]
    print(f"  {path}: {len(files)}개 파일, {sum(map(len, frames))}행")
    return pd.concat(frames, ignore_index=True)


_KR_TIME = re.compile(
    r"^\s*(\d{4})[-./](\d{1,2})[-./](\d{1,2})\s*(오전|오후|AM|PM|am|pm)\s*"
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")


def parse_time(s):
    """'2026-09-14 오후 4:14:59' 형식(및 일반 24시간 형식) 을 datetime 으로 변환."""
    s = s.astype("string")
    parts = s.str.extract(_KR_TIME)
    ok = parts[0].notna()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if ok.any():
        p = parts[ok]
        hour = p[4].astype(int) % 12
        hour = hour + np.where(p[3].str.upper().isin(["오후", "PM"]), 12, 0)
        out[ok] = pd.to_datetime(pd.DataFrame({
            "year": p[0].astype(int), "month": p[1].astype(int), "day": p[2].astype(int),
            "hour": hour, "minute": p[5].astype(int),
            "second": p[6].fillna("0").astype(int)}), errors="coerce")
    rest = ~ok & s.notna()
    if rest.any():
        out[rest] = pd.to_datetime(s[rest], errors="coerce")
    return out


def load_pressure(raw):
    df = raw.copy()
    df["time"] = parse_time(df["time"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    bad = df["time"].isna() | df["value"].isna()
    if bad.any():
        print(f"  대기압: 시간/값을 읽지 못한 {bad.sum()}행 제외")
    df = df[~bad].drop_duplicates("time").sort_values("time").reset_index(drop=True)
    return df.rename(columns={"value": "pressure"})


def load_soh(raw):
    df = raw.copy()
    if ITEM_FILTER is not None:
        before = len(df)
        df = df[df["item"].str.upper().eq(ITEM_FILTER.upper()).fillna(False)]
        print(f"  SOH: ITEM_ID={ITEM_FILTER} 필터 {before} -> {len(df)}행")
    df["time"] = parse_time(df["time"])
    df["thickness"] = pd.to_numeric(df["value"], errors="coerce")
    bad = df["time"].isna() | df["thickness"].isna()
    if bad.any():
        print(f"  SOH: 시간/두께를 읽지 못한 {bad.sum()}행 제외")
    df = df[~bad].drop(columns=["value", "item"]).drop_duplicates()
    return df.sort_values("time").reset_index(drop=True)


# ============================================================
# Impala 조회
# ============================================================
DB_CONFIG = ROOT / "db_config.ini"
QUERY_DIR = ROOT / "queries"


def connect_impala(config_path=DB_CONFIG):
    import configparser
    try:
        from impala.dbapi import connect
    except ImportError:
        raise SystemExit("[오류] impyla 가 설치되어 있지 않습니다: pip install impyla")
    if not Path(config_path).exists():
        raise SystemExit(f"[오류] 접속 설정 파일이 없습니다: {config_path}")
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.read(config_path, encoding="utf-8")
    c = cp["impala"]
    kw = dict(host=c.get("host"), port=c.getint("port", 21050),
              auth_mechanism=c.get("auth_mechanism", "NOSASL").upper(),
              use_ssl=c.getboolean("use_ssl", False), timeout=c.getint("timeout", 600))
    if c.get("database"):
        kw["database"] = c.get("database")
    if c.get("user"):
        kw["user"] = c.get("user")
    print(f"  Impala 접속: {kw['host']}:{kw['port']} (auth={kw['auth_mechanism']})")
    try:
        return connect(**kw)
    except Exception as e:
        raise SystemExit(f"[오류] Impala 접속 실패: {e}\n"
                         f"       db_config.ini 의 host/port 를 확인하세요. "
                         f"계속 실패하면 auth_mechanism 을 PLAIN 으로 바꿔보세요.")


def render_sql(path, start, end):
    """SQL 파일의 {start} {end} {start_ymd} {end_ymd} 를 기간으로 치환. end 는 미포함."""
    sql = Path(path).read_text(encoding="utf-8")
    sql = sql.format(start=start.strftime("%Y-%m-%d %H:%M:%S"),
                     end=end.strftime("%Y-%m-%d %H:%M:%S"),
                     start_ymd=start.strftime("%Y%m%d"),
                     end_ymd=(end - pd.Timedelta(days=1)).strftime("%Y%m%d"))
    return sql.strip().rstrip(";")


def run_query(conn, sql, name):
    cur = conn.cursor()
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    except Exception as e:
        raise SystemExit(f"[오류] {name} 쿼리 실패: {e}\n--- SQL ---\n{sql}")
    finally:
        cur.close()
    print(f"  {name}: {len(df)}행 조회")
    return df


def fetch_from_db(start, end, out_dir, conn=None):
    conn = conn or connect_impala()
    raw = {}
    try:
        # 대기압은 시차 분석/직전 평균에 쓰도록 시작 시각보다 앞부분까지 조회
        margin = pd.Timedelta(minutes=max(LAG_MAX_MIN, WINDOW_MIN) + MATCH_TOLERANCE_MIN)
        for name in ("pressure", "soh"):
            q_start = start - margin if name == "pressure" else start
            sql = render_sql(QUERY_DIR / f"{name}.sql", q_start, end)
            df = run_query(conn, sql, name)
            df.to_csv(out_dir / f"raw_{name}.csv", index=False, encoding="utf-8-sig")
            raw[name] = pick_columns(df, COLUMNS[name], f"{name}.sql 조회 결과")
    finally:
        conn.close()
    return raw["pressure"], raw["soh"]


# ============================================================
# 매칭
# ============================================================
def _asof(left_time, pressure, col, tol_min, direction="nearest"):
    left = pd.DataFrame({"t": left_time.values, "_i": np.arange(len(left_time))}).sort_values("t")
    m = pd.merge_asof(left, pressure[["time", col]].rename(columns={"time": "t"}),
                      on="t", direction=direction,
                      tolerance=pd.Timedelta(minutes=tol_min))
    return m.sort_values("_i")[col].to_numpy()


def match(soh, pressure):
    p = pressure.set_index("time")
    roll = p["pressure"].rolling(f"{WINDOW_MIN}min").mean().rename("roll").reset_index()

    df = soh.copy()
    df["pressure"] = _asof(df["time"], pressure, "pressure", MATCH_TOLERANCE_MIN)
    tmp = pressure.rename(columns={"time": "p_time"}).assign(time=pressure["time"])
    df["pressure_time"] = _asof(df["time"], tmp, "p_time", MATCH_TOLERANCE_MIN)
    df[f"pressure_avg_{WINDOW_MIN}m"] = _asof(df["time"], roll, "roll",
                                              MATCH_TOLERANCE_MIN, "backward")
    before = _asof(df["time"] - pd.Timedelta(minutes=WINDOW_MIN), pressure, "pressure",
                   MATCH_TOLERANCE_MIN)
    df[f"pressure_change_{WINDOW_MIN}m"] = df["pressure"] - before
    return df


# ============================================================
# 분석
# ============================================================
def regress(x, y):
    from scipy import stats
    m = x.notna() & y.notna()
    x, y = x[m].astype(float), y[m].astype(float)
    n = len(x)
    row = {"N": n, "기울기(Å/hPa)": np.nan, "절편": np.nan, "R²": np.nan,
           "Pearson r": np.nan, "p-value": np.nan, "Spearman ρ": np.nan,
           "두께 평균(Å)": y.mean() if n else np.nan, "두께 σ(Å)": y.std() if n > 1 else np.nan,
           "대기압 min": x.min() if n else np.nan, "대기압 max": x.max() if n else np.nan}
    if n >= MIN_N_FOR_STATS and x.nunique() > 1:
        r = stats.linregress(x, y)
        row.update({"기울기(Å/hPa)": r.slope, "절편": r.intercept, "R²": r.rvalue ** 2,
                    "Pearson r": r.rvalue, "p-value": r.pvalue,
                    "Spearman ρ": stats.spearmanr(x, y).statistic})
    return row


def summary_table(df):
    rows = [{"구분": "전체 (두께 원값)", **regress(df["pressure"], df["thickness"])},
            {"구분": "전체 (설비 평균 제거)", **regress(df["pressure"], df["thickness_dev"])}]
    for eqp, g in df.groupby("eqp"):
        rows.append({"구분": f"설비 {eqp}", **regress(g["pressure"], g["thickness"])})
    return pd.DataFrame(rows)


def bin_table(df):
    lo = np.floor(df["pressure"].min() / PRESSURE_BIN_HPA) * PRESSURE_BIN_HPA
    hi = np.ceil(df["pressure"].max() / PRESSURE_BIN_HPA) * PRESSURE_BIN_HPA + PRESSURE_BIN_HPA
    edges = np.arange(lo, hi + 1e-9, PRESSURE_BIN_HPA)
    d = df.assign(대기압구간=pd.cut(df["pressure"], edges, right=False))
    d["대기압구간"] = d["대기압구간"].astype(str)
    agg = {"thickness": ["count", "mean", "std", "min", "max"], "thickness_dev": ["mean"]}
    all_ = d.groupby("대기압구간", sort=False).agg(agg)
    all_.insert(0, "설비", "전체")
    per = d.groupby(["eqp", "대기압구간"], sort=False).agg(agg).reset_index(level=0)
    per = per.rename(columns={"eqp": "설비"})
    out = pd.concat([all_, per])
    out.columns = ["설비", "웨이퍼수", "두께 평균", "두께 σ", "두께 min", "두께 max", "설비편차 평균"]
    out = out.reset_index().rename(columns={"index": "대기압구간"})
    out["_k"] = out["대기압구간"].str.extract(r"([\d.]+)").astype(float)
    return out.sort_values(["설비", "_k"]).drop(columns="_k").reset_index(drop=True)


def lag_table(soh, pressure):
    rows = []
    for lag in range(0, LAG_MAX_MIN + 1, LAG_STEP_MIN):
        p = _asof(soh["time"] - pd.Timedelta(minutes=lag), pressure, "pressure",
                  MATCH_TOLERANCE_MIN)
        dev = soh["thickness"] - soh.groupby("eqp")["thickness"].transform("mean")
        r = regress(pd.Series(p, index=soh.index), dev)
        rows.append({"시차(분, TKIN 이전)": lag, "N": r["N"], "Pearson r": r["Pearson r"],
                     "R²": r["R²"], "기울기(Å/hPa)": r["기울기(Å/hPa)"]})
    return pd.DataFrame(rows)


# ============================================================
# 그래프
# ============================================================
def make_charts(df, pressure, summary, lag, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"figure.dpi": 110, "axes.grid": True, "grid.alpha": 0.3,
                         "axes.spines.top": False, "axes.spines.right": False})
    colors = plt.get_cmap("tab10")
    eqps = sorted(df["eqp"].unique())
    cmap = {e: colors(i % 10) for i, e in enumerate(eqps)}
    files = []

    def save(fig, name):
        p = out_dir / name
        fig.tight_layout()
        fig.savefig(p)
        plt.close(fig)
        files.append(p)

    # 1. 시계열
    fig, ax1 = plt.subplots(figsize=(13, 5))
    ax2 = ax1.twinx()
    ax2.plot(pressure["time"], pressure["pressure"], color="0.55", lw=1, label="Pressure")
    ax2.set_ylabel("Atmospheric pressure (hPa)")
    ax2.spines["right"].set_visible(True)
    ax2.grid(False)
    for e in eqps:
        g = df[df["eqp"] == e]
        ax1.scatter(g["time"], g["thickness"], s=12, color=cmap[e], label=e, zorder=3)
    ax1.set_ylabel("SOH thickness (Å)")
    ax1.set_title("Pressure & SOH thickness over time")
    ax1.set_zorder(ax2.get_zorder() + 1)
    ax1.patch.set_visible(False)
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8, ncol=len(eqps) + 1)
    save(fig, "1_timeseries.png")

    # 2. 산점도 (설비별)
    n = len(eqps)
    fig, axes = plt.subplots(1, n + 1, figsize=(4.2 * (n + 1), 4.2), squeeze=False)
    axes = axes[0]
    ax = axes[0]
    for e in eqps:
        g = df[df["eqp"] == e]
        ax.scatter(g["pressure"], g["thickness_dev"], s=10, color=cmap[e], label=e, alpha=0.7)
    _fit_line(ax, df["pressure"], df["thickness_dev"], "k")
    ax.set_title("All EQP (thickness − EQP mean)")
    ax.set_xlabel("Pressure (hPa)")
    ax.set_ylabel("Δ thickness (Å)")
    ax.legend(fontsize=8)
    for ax, e in zip(axes[1:], eqps):
        g = df[df["eqp"] == e]
        ax.scatter(g["pressure"], g["thickness"], s=10, color=cmap[e], alpha=0.7)
        _fit_line(ax, g["pressure"], g["thickness"], cmap[e])
        ax.set_title(e)
        ax.set_xlabel("Pressure (hPa)")
        ax.set_ylabel("Thickness (Å)")
    save(fig, "2_scatter.png")

    # 3. 대기압 구간별 박스플롯
    lo = np.floor(df["pressure"].min() / PRESSURE_BIN_HPA) * PRESSURE_BIN_HPA
    edges = np.arange(lo, df["pressure"].max() + PRESSURE_BIN_HPA, PRESSURE_BIN_HPA)
    b = pd.cut(df["pressure"], edges, right=False)
    groups = [(f"{iv.left:g}", g["thickness_dev"].to_numpy())
              for iv, g in df.groupby(b, observed=True) if len(g)]
    fig, ax = plt.subplots(figsize=(max(6, 0.7 * len(groups) + 2), 4.5))
    ax.boxplot([g for _, g in groups], showfliers=False)
    ax.set_xticks(range(1, len(groups) + 1), [l for l, _ in groups], rotation=45)
    for i, (_, g) in enumerate(groups, 1):
        ax.text(i, ax.get_ylim()[1], f"n={len(g)}", ha="center", va="bottom", fontsize=7)
    ax.set_xlabel(f"Pressure bin start (hPa, width {PRESSURE_BIN_HPA:g})")
    ax.set_ylabel("Δ thickness vs EQP mean (Å)")
    ax.set_title("Thickness by pressure bin", pad=16)
    save(fig, "3_boxplot_by_bin.png")

    # 4. 설비별 민감도
    s = summary[summary["구분"].str.startswith("설비 ")]
    fig, ax = plt.subplots(figsize=(max(5, 1.1 * len(s) + 2), 4))
    names = s["구분"].str.replace("설비 ", "", regex=False)
    ax.bar(names, s["기울기(Å/hPa)"], color=[cmap.get(e, "C0") for e in names])
    for i, (v, r2) in enumerate(zip(s["기울기(Å/hPa)"], s["R²"])):
        if pd.notna(v):
            ax.text(i, v, f"{v:.2f}\nR²={r2:.2f}", ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=8)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylabel("Slope (Å / hPa)")
    ax.set_title("Pressure sensitivity by EQP")
    save(fig, "4_sensitivity_by_eqp.png")

    # 5. 시차 분석
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(lag["시차(분, TKIN 이전)"], lag["Pearson r"], marker="o")
    best = lag.loc[lag["Pearson r"].abs().idxmax()] if lag["Pearson r"].notna().any() else None
    if best is not None:
        ax.axvline(best["시차(분, TKIN 이전)"], color="C3", ls="--",
                   label=f"max |r| at {best['시차(분, TKIN 이전)']:.0f} min")
        ax.legend()
    ax.set_xlabel("Lag: pressure taken N min before TKIN_TIME")
    ax.set_ylabel("Pearson r")
    ax.set_title("Lag analysis")
    save(fig, "5_lag.png")
    return files


def _fit_line(ax, x, y, color):
    m = x.notna() & y.notna()
    if m.sum() >= MIN_N_FOR_STATS and x[m].nunique() > 1:
        k, b = np.polyfit(x[m], y[m], 1)
        xs = np.linspace(x[m].min(), x[m].max(), 50)
        ax.plot(xs, k * xs + b, color=color, lw=1.5, label=f"{k:.2f} Å/hPa")
        ax.legend(fontsize=8)


# ============================================================
# 엑셀
# ============================================================
def write_excel(path, df, unmatched, summary, bins, lag, charts):
    from openpyxl.drawing.image import Image
    from openpyxl.utils import get_column_letter

    c = COLUMNS["soh"]
    data = df.rename(columns={
        "lot": c["lot"], "wafer": c["wafer"], "eqp": c["eqp"], "time": c["time"],
        "thickness": "두께(Å)", "pressure": "대기압(hPa)", "pressure_time": "매칭된 대기압 시간",
        f"pressure_avg_{WINDOW_MIN}m": f"직전{WINDOW_MIN}분 평균 대기압",
        f"pressure_change_{WINDOW_MIN}m": f"{WINDOW_MIN}분간 대기압 변화",
        "eqp_mean": "설비 평균 두께", "thickness_dev": "설비 평균 대비 편차(Å)"})
    um = unmatched.rename(columns={"lot": c["lot"], "wafer": c["wafer"], "eqp": c["eqp"],
                                   "time": c["time"], "thickness": "두께(Å)"})
    settings = pd.DataFrame({"항목": [
        "매칭 기준 시간", "ITEM_ID 필터", "매칭 허용 오차(분)", "평균/변화량 구간(분)",
        "대기압 구간 폭(hPa)", "매칭 웨이퍼 수", "매칭 실패 웨이퍼 수"], "값": [
        c["time"], ITEM_FILTER, MATCH_TOLERANCE_MIN, WINDOW_MIN, PRESSURE_BIN_HPA,
        len(df), len(unmatched)]})

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="요약_회귀", index=False)
        bins.to_excel(xw, sheet_name="대기압구간별", index=False)
        lag.to_excel(xw, sheet_name="시차분석", index=False)
        data.to_excel(xw, sheet_name="매칭데이터", index=False)
        um.to_excel(xw, sheet_name="매칭실패", index=False)
        settings.to_excel(xw, sheet_name="설정", index=False)
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
            for i, col in enumerate(ws.iter_cols(min_row=1, max_row=min(ws.max_row, 200)), 1):
                width = max(len(str(v.value)) if v.value is not None else 0 for v in col)
                ws.column_dimensions[get_column_letter(i)].width = min(max(10, width * 1.3), 40)
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, float):
                        cell.number_format = "0.000"
                    elif hasattr(cell.value, "hour"):
                        cell.number_format = "yyyy-mm-dd hh:mm:ss"
        ws = xw.book.create_sheet("그래프", 0)
        row = 1
        for f in charts:
            img = Image(str(f))
            ws.add_image(img, f"A{row}")
            row += int(img.height / 20) + 2


# ============================================================
# main
# ============================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="대기압 vs HSOH 두께 분석")
    ap.add_argument("--pressure", default=ROOT / "input" / "pressure",
                    help="대기압 CSV 파일 또는 폴더 (기본: input/pressure)")
    ap.add_argument("--soh", default=ROOT / "input" / "soh",
                    help="SOH 두께 CSV 파일 또는 폴더 (기본: input/soh)")
    ap.add_argument("--out", default=ROOT / "output", help="결과 폴더 (기본: output)")
    ap.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                    help="지정하면 CSV 대신 Impala 에서 조회 (시작일)")
    ap.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD",
                    help="Impala 조회 종료일 (이 날짜 포함)")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            raise SystemExit("[오류] --from 과 --to 를 함께 지정하세요.")
        try:
            start = pd.Timestamp(args.date_from).normalize()
            end = pd.Timestamp(args.date_to).normalize() + pd.Timedelta(days=1)
        except ValueError:
            raise SystemExit("[오류] 날짜는 YYYY-MM-DD 형식으로 입력하세요.")
        if end <= start:
            raise SystemExit("[오류] --to 가 --from 보다 빠릅니다.")
        print(f"[1/4] Impala 조회 ({start:%Y-%m-%d} ~ {end - pd.Timedelta(days=1):%Y-%m-%d})")
        raw_p, raw_s = fetch_from_db(start, end, out_dir)
    else:
        print("[1/4] CSV 읽기")
        raw_p = read_all(args.pressure, COLUMNS["pressure"])
        raw_s = read_all(args.soh, COLUMNS["soh"])
    pressure = load_pressure(raw_p)
    soh = load_soh(raw_s)
    if pressure.empty or soh.empty:
        raise SystemExit("[오류] 사용할 수 있는 데이터가 없습니다.")
    print(f"  대기압 {pressure['time'].min()} ~ {pressure['time'].max()} ({len(pressure)}개)")
    print(f"  SOH    {soh['time'].min()} ~ {soh['time'].max()} ({len(soh)}웨이퍼)")

    print("[2/4] 시간 매칭")
    merged = match(soh, pressure)
    unmatched = merged[merged["pressure"].isna()][["lot", "wafer", "eqp", "time", "thickness"]]
    df = merged[merged["pressure"].notna()].copy()
    df["eqp_mean"] = df.groupby("eqp")["thickness"].transform("mean")
    df["thickness_dev"] = df["thickness"] - df["eqp_mean"]
    print(f"  매칭 {len(df)} / 실패 {len(unmatched)} (허용 ±{MATCH_TOLERANCE_MIN}분)")
    if df.empty:
        raise SystemExit("[오류] 매칭된 웨이퍼가 없습니다. 두 데이터의 기간을 확인하세요.")

    print("[3/4] 분석")
    summary = summary_table(df)
    bins = bin_table(df)
    lag = lag_table(df, pressure)

    print("[4/4] 결과 저장")
    charts = make_charts(df, pressure, summary, lag, out_dir)
    xlsx = out_dir / "pressure_vs_soh_report.xlsx"
    try:
        write_excel(xlsx, df, unmatched, summary, bins, lag, charts)
    except PermissionError:
        raise SystemExit(f"[오류] {xlsx} 가 열려 있어 저장할 수 없습니다. 엑셀을 닫고 다시 실행하세요.")

    pd.set_option("display.width", 200)
    print("\n=== 대기압 1 hPa 당 두께 변화 ===")
    print(summary[["구분", "N", "기울기(Å/hPa)", "R²", "p-value"]]
          .to_string(index=False, float_format=lambda v: f"{v:.4g}"))
    print(f"\n결과: {xlsx}")
    for f in charts:
        print(f"      {f}")


if __name__ == "__main__":
    sys.exit(main())

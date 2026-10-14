"""대기압 vs HSOH 두께 분석.

데이터 3개를 연결해서 분석한다.
    쿼리1 대기압      : 시간, 대기압
    쿼리2 계측        : LOT, WAFER, ITEM, 두께 (+ 계측시간)
    쿼리3 공정 진행   : LOT, 설비, TKIN_TIME (+ WAFER)
    쿼리2 + 쿼리3 은 LOT(있으면 WAFER 까지)으로 연결 -> 웨이퍼별 설비, TKIN_TIME
    그 TKIN_TIME 에 가장 가까운 쿼리1 대기압을 붙인다.

사용법 (VS Code 에서 F5 또는 python analyze.py)
    1) 아래 "설정" 에서 기간(DATE_FROM/DATE_TO), SQL 3개, COLUMNS 를 채운다.
    2) 실행하면 bigdataquery.getData() 로 조회 -> 연결 -> 분석 -> output/ 에 리포트 생성.
       조회 원본은 output/raw_pressure.csv, raw_meas.csv, raw_proc.csv 로도 저장된다.

    DATA_SOURCE = "csv" 로 바꾸면 조회 대신 input/pressure/, input/meas/, input/proc/ 의 CSV 를 읽는다.
    명령줄로 덮어쓰기:  python analyze.py --from 2026-09-01 --to 2026-09-30
                        python analyze.py --source csv

조회 결과/CSV 에 컬럼이 많아도 COLUMNS 에 지정한 컬럼만 추출해서 사용한다.
"""
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# 설정 (실제 데이터에 맞게 수정)
# ============================================================
DATA_SOURCE = "query"            # "query": bigdataquery 로 조회 / "csv": input 폴더 CSV 사용
DATE_FROM = "2026-09-01"         # 분석 시작일 (계측 기준, 포함)
DATE_TO = "2026-09-30"           # 분석 종료일 (계측 기준, 포함)
PROC_LOOKBACK_DAYS = 3           # 공정 진행 조회를 계측 시작일보다 며칠 앞부터 할지

# 조회 SQL. 아래 자리표시자는 실행할 때 자동으로 바뀐다.
#   {start}, {end}         -> '2026-09-01 00:00:00' 형식 (end 는 종료일 다음날 0시, 미포함)
#   {start_ymd}, {end_ymd} -> '20260901' 형식 (날짜 파티션 컬럼용, 둘 다 포함)
#   {lots}                 -> 쿼리2 에서 나온 LOT 목록 'LOT1','LOT2',... (쿼리3 에서만 사용 가능)
# 쿼리1(대기압) 의 기간은 쿼리3 에서 나온 TKIN_TIME 범위에 맞춰 자동으로 정해진다.
# 쿼리3 의 {start} 는 계측 시작일 - PROC_LOOKBACK_DAYS 이다.

# 쿼리1: 대기압
SQL_PRESSURE = """
select *
from 대기압쿼리명
where 1=1
  and act_time >= '{start}'
  and act_time <  '{end}'
"""

# 쿼리2: LOT - 계측 데이터
SQL_MEAS = """
select *
from 계측쿼리명
where 1=1
  and ITEM_ID = 'AVG'
  and MEAS_TIME >= '{start}'
  and MEAS_TIME <  '{end}'
"""

# 쿼리3: LOT - 공정 진행 정보 (HSOH 공정만 나오도록 STEP 조건을 꼭 넣을 것)
SQL_PROC = """
select *
from 공정진행쿼리명
where 1=1
  and LOT_ID in ({lots})
  -- and STEP_ID = 'HSOH 공정 STEP'
"""

# 각 쿼리 결과에서 사용할 컬럼명. None 은 "그 컬럼 없음" 이라는 뜻.
COLUMNS = {
    "pressure": {                # 쿼리1
        "time": "act_time",      # 대기압 측정 시간
        "value": "value",        # 대기압 값 (hPa)
    },
    "meas": {                    # 쿼리2
        "lot": "LOT_ID",
        "wafer": "WAFER_ID",
        "item": "ITEM_ID",
        "value": "FAB_VALUE",    # 두께 (Å)
        "meas_time": "MEAS_TIME",  # 계측 시간 (없으면 None)
    },
    "proc": {                    # 쿼리3
        "lot": "LOT_ID",
        "eqp": "EQP_ID",
        "time": "TKIN_TIME",     # 공정 진행 시간 -> 대기압 매칭 기준
        "wafer": None,           # 공정 정보가 WAFER 단위면 컬럼명 입력 (LOT 단위면 None)
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
ROOT = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
SOURCES = {"pressure": "쿼리1(대기압)", "meas": "쿼리2(계측)", "proc": "쿼리3(공정)"}


# ============================================================
# 읽기
# ============================================================
def _norm(name):
    """컬럼명 비교용: 대소문자, 공백, '_', '-', '.' 차이는 무시 (LOT ID = lot_id = LOT-ID)."""
    return re.sub(r"[\s_\-.\ufeff]+", "", str(name)).upper()


def _wanted(kind):
    return {k: v for k, v in COLUMNS[kind].items() if v}


def pick_columns(df, kind, src, all_cols=None):
    """df 에서 COLUMNS[kind] 에 지정한 컬럼만 골라 내부이름으로 바꿔 반환."""
    wanted = _wanted(kind)
    targets = {_norm(v): k for k, v in wanted.items()}
    all_cols = df.columns if all_cols is None else all_cols
    df = df[[c for c in df.columns if _norm(c) in targets]]
    df = df.rename(columns=lambda c: targets[_norm(c)])
    df = df.loc[:, ~df.columns.duplicated()]
    missing = [wanted[k] for k in wanted if k not in df.columns]
    if missing:
        raise SystemExit(f"[오류] {src} 에 필요한 컬럼이 없습니다: {missing}\n"
                         f"       실제 컬럼: {list(all_cols)}\n"
                         f"       analyze.py 상단 COLUMNS 설정(또는 SQL 의 AS 이름)을 확인하세요.")
    return df[list(wanted)].astype("string").apply(lambda x: x.str.strip())


def read_csv_columns(path, kind):
    """CSV 에서 COLUMNS[kind] 컬럼만 읽는다. 인코딩은 자동 판별."""
    targets = {_norm(v) for v in _wanted(kind).values()}
    last_err = None
    for enc in ENCODINGS:
        try:
            header = pd.read_csv(path, encoding=enc, nrows=0).columns
            df = pd.read_csv(path, encoding=enc, dtype=str,
                             usecols=lambda c: _norm(c) in targets)
            break
        except UnicodeDecodeError as e:
            last_err = e
    else:
        raise SystemExit(f"[오류] {path} 인코딩을 읽을 수 없습니다: {last_err}")
    return pick_columns(df, kind, path, header)


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


def read_all(path, kind):
    files = collect_csvs(path)
    frames = [read_csv_columns(f, kind) for f in files]
    print(f"  {SOURCES[kind]} {path}: {len(files)}개 파일, {sum(map(len, frames))}행")
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


def load_meas(raw):
    df = raw.copy()
    if ITEM_FILTER is not None:
        before = len(df)
        df = df[df["item"].str.upper().eq(ITEM_FILTER.upper()).fillna(False)]
        print(f"  계측: ITEM_ID={ITEM_FILTER} 필터 {before} -> {len(df)}행")
    df["thickness"] = pd.to_numeric(df["value"], errors="coerce")
    if "meas_time" in df:
        df["meas_time"] = parse_time(df["meas_time"])
    bad = df["thickness"].isna() | df["lot"].isna() | df["wafer"].isna()
    if bad.any():
        print(f"  계측: LOT/WAFER/두께를 읽지 못한 {bad.sum()}행 제외")
    df = df[~bad].drop(columns=["value", "item"]).drop_duplicates()
    return df.reset_index(drop=True)


def load_proc(raw):
    df = raw.copy()
    df["time"] = parse_time(df["time"])
    bad = df["time"].isna() | df["lot"].isna()
    if bad.any():
        print(f"  공정: LOT/TKIN_TIME 을 읽지 못한 {bad.sum()}행 제외")
    return df[~bad].drop_duplicates().reset_index(drop=True)


# ============================================================
# bigdataquery 조회
# ============================================================
def render_sql(sql, start, end, lots=None):
    """SQL 의 자리표시자를 치환. end 는 미포함."""
    rep = {"{start}": start.strftime("%Y-%m-%d %H:%M:%S"),
           "{end}": end.strftime("%Y-%m-%d %H:%M:%S"),
           "{start_ymd}": start.strftime("%Y%m%d"),
           "{end_ymd}": (end - pd.Timedelta(seconds=1)).strftime("%Y%m%d")}
    if lots is not None:
        rep["{lots}"] = ",".join("'" + str(x).replace("'", "''") + "'" for x in lots) or "''"
    for k, v in rep.items():
        sql = sql.replace(k, v)
    return sql


def run_query(sql, name):
    try:
        from bigdataquery import getData
    except ImportError:
        raise SystemExit("[오류] bigdataquery 를 불러올 수 없습니다. "
                         "평소 getData 를 쓰던 파이썬 환경(인터프리터)에서 실행하세요.")
    try:
        df = getData(sql)
    except Exception as e:
        raise SystemExit(f"[오류] {name} 조회 실패: {e}\n--- SQL ---\n{sql}")
    if df is None:
        raise SystemExit(f"[오류] {name} 조회 결과가 없습니다.\n--- SQL ---\n{sql}")
    df = df if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
    print(f"  {name}: {len(df)}행 조회")
    return df


def _fetch(kind, sql, start, end, out_dir, lots=None):
    df = run_query(render_sql(sql, start, end, lots), SOURCES[kind])
    df.to_csv(out_dir / f"raw_{kind}.csv", index=False, encoding="utf-8-sig")
    return pick_columns(df, kind, f"{SOURCES[kind]} 조회 결과")


def fetch_from_query(start, end, out_dir):
    """쿼리2 -> 쿼리3 -> 쿼리1 순서로 조회 (앞 결과로 다음 조회 범위를 정한다)."""
    meas = _fetch("meas", SQL_MEAS, start, end, out_dir)
    if meas.empty:
        raise SystemExit("[오류] 쿼리2(계측) 조회 결과가 0행입니다. 기간/조건을 확인하세요.")
    lots = sorted(meas["lot"].dropna().str.strip().unique())
    proc = _fetch("proc", SQL_PROC, start - pd.Timedelta(days=PROC_LOOKBACK_DAYS), end,
                  out_dir, lots)
    if proc.empty:
        raise SystemExit("[오류] 쿼리3(공정) 조회 결과가 0행입니다. 기간/조건을 확인하세요.")

    # 대기압은 TKIN_TIME 범위 + 시차 분석/직전 평균에 필요한 앞부분까지 조회
    t = parse_time(proc["time"]).dropna()
    margin = pd.Timedelta(minutes=max(LAG_MAX_MIN, WINDOW_MIN) + MATCH_TOLERANCE_MIN)
    p_start = (t.min() - margin).floor("min")
    p_end = (t.max() + pd.Timedelta(minutes=MATCH_TOLERANCE_MIN + 1)).ceil("min")
    pressure = _fetch("pressure", SQL_PRESSURE, p_start, p_end, out_dir)
    return pressure, meas, proc


# ============================================================
# 연결 (쿼리2 + 쿼리3)
# ============================================================
def join_meas_proc(meas, proc):
    """계측에 공정 정보(설비, TKIN_TIME)를 붙인다.

    LOT(공정 정보에 WAFER 가 있으면 LOT+WAFER)으로 연결한다.
    한 웨이퍼에 공정 이력이 여러 건이면 계측 시간 이전의 가장 늦은 TKIN_TIME 을 쓴다.
    (계측 시간이 없으면 가장 늦은 TKIN_TIME)
    """
    keys = ["lot", "wafer"] if "wafer" in proc else ["lot"]
    m = meas.reset_index(names="_mid")
    j = m.merge(proc, on=keys, how="inner")
    n_multi = (j.groupby("_mid").size() > 1).sum()
    if "meas_time" in j:
        j = j[~(j["meas_time"].notna() & (j["time"] > j["meas_time"]))]
    j = j.sort_values("time").drop_duplicates("_mid", keep="last")
    if n_multi:
        print(f"  공정 이력이 2건 이상인 웨이퍼 {n_multi}개 -> 계측 이전 가장 늦은 TKIN_TIME 사용")
    joined = j.sort_values("_mid").drop(columns="_mid").reset_index(drop=True)
    missing = m[~m["_mid"].isin(j["_mid"])].drop(columns="_mid").reset_index(drop=True)
    return joined, missing


# ============================================================
# 대기압 매칭
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

    cm, cp = COLUMNS["meas"], COLUMNS["proc"]
    names = {"lot": cm["lot"], "wafer": cm["wafer"], "eqp": cp["eqp"], "time": cp["time"],
             "meas_time": cm.get("meas_time") or "계측시간", "thickness": "두께(Å)"}
    data = df.rename(columns={
        **names, "pressure": "대기압(hPa)", "pressure_time": "매칭된 대기압 시간",
        f"pressure_avg_{WINDOW_MIN}m": f"직전{WINDOW_MIN}분 평균 대기압",
        f"pressure_change_{WINDOW_MIN}m": f"{WINDOW_MIN}분간 대기압 변화",
        "eqp_mean": "설비 평균 두께", "thickness_dev": "설비 평균 대비 편차(Å)"})
    um = unmatched.rename(columns=names)
    settings = pd.DataFrame({"항목": [
        "쿼리2-3 연결 키", "대기압 매칭 기준 시간", "ITEM_ID 필터", "매칭 허용 오차(분)",
        "평균/변화량 구간(분)", "대기압 구간 폭(hPa)", "매칭 웨이퍼 수", "매칭 실패 웨이퍼 수"], "값": [
        "LOT+WAFER" if cp.get("wafer") else "LOT", cp["time"], ITEM_FILTER,
        MATCH_TOLERANCE_MIN, WINDOW_MIN, PRESSURE_BIN_HPA, len(df), len(unmatched)]})

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
    ap.add_argument("--source", choices=["query", "csv"], default=DATA_SOURCE,
                    help=f"데이터 가져오는 방법 (기본: {DATA_SOURCE})")
    ap.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                    help=f"분석 시작일, 계측 기준 (조회 모드 기본: {DATE_FROM}, csv 모드 기본: 전체)")
    ap.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD",
                    help=f"분석 종료일, 포함 (조회 모드 기본: {DATE_TO}, csv 모드 기본: 전체)")
    for kind, folder in (("pressure", "pressure"), ("meas", "meas"), ("proc", "proc")):
        ap.add_argument(f"--{kind}", default=ROOT / "input" / folder,
                        help=f"csv 모드: {SOURCES[kind]} CSV 파일 또는 폴더 (기본: input/{folder})")
    ap.add_argument("--out", default=ROOT / "output", help="결과 폴더 (기본: output)")
    args, _ = ap.parse_known_args(argv)  # 주피터 커널 인자는 무시

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.source == "query":
        args.date_from = args.date_from or DATE_FROM
        args.date_to = args.date_to or DATE_TO
    start = end = None
    if args.date_from or args.date_to:
        try:
            start = pd.Timestamp(args.date_from).normalize() if args.date_from else None
            end = (pd.Timestamp(args.date_to).normalize() + pd.Timedelta(days=1)
                   if args.date_to else None)
        except ValueError:
            raise SystemExit("[오류] 날짜는 YYYY-MM-DD 형식으로 입력하세요.")
        if start is not None and end is not None and end <= start:
            raise SystemExit("[오류] 종료일이 시작일보다 빠릅니다.")

    if args.source == "query":
        if start is None or end is None:
            raise SystemExit("[오류] 조회 모드에서는 DATE_FROM / DATE_TO 가 필요합니다.")
        print(f"[1/5] 조회 ({start:%Y-%m-%d} ~ {end - pd.Timedelta(days=1):%Y-%m-%d})")
        raw_p, raw_m, raw_c = fetch_from_query(start, end, out_dir)
    else:
        print("[1/5] CSV 읽기")
        raw_p = read_all(args.pressure, "pressure")
        raw_m = read_all(args.meas, "meas")
        raw_c = read_all(args.proc, "proc")
    pressure = load_pressure(raw_p)
    meas = load_meas(raw_m)
    proc = load_proc(raw_c)
    if "meas_time" in meas and (start is not None or end is not None):
        mt = meas["meas_time"]
        keep = pd.Series(True, index=meas.index)
        if start is not None:
            keep &= mt.isna() | (mt >= start)
        if end is not None:
            keep &= mt.isna() | (mt < end)
        meas = meas[keep].reset_index(drop=True)
    if pressure.empty or meas.empty or proc.empty:
        raise SystemExit("[오류] 사용할 수 있는 데이터가 없습니다. "
                         f"(대기압 {len(pressure)} / 계측 {len(meas)} / 공정 {len(proc)}행)")

    print("[2/5] 계측 + 공정 연결 (LOT" + (", WAFER" if "wafer" in proc else "") + ")")
    soh, no_proc = join_meas_proc(meas, proc)
    print(f"  연결 {len(soh)} / 공정정보 없음 {len(no_proc)} (계측 웨이퍼 {len(meas)})")
    if soh.empty:
        raise SystemExit("[오류] 계측과 공정 정보가 연결된 웨이퍼가 없습니다. "
                         "두 쿼리의 LOT 값 형식이 같은지 확인하세요.")
    print(f"  대기압 {pressure['time'].min()} ~ {pressure['time'].max()} ({len(pressure)}개)")
    print(f"  TKIN   {soh['time'].min()} ~ {soh['time'].max()} ({len(soh)}웨이퍼)")

    print("[3/5] 대기압 매칭 (TKIN_TIME 기준)")
    merged = match(soh, pressure)
    cols = ["lot", "wafer", "eqp", "time", "meas_time", "thickness"]
    no_p = merged[merged["pressure"].isna()].assign(사유="대기압 없음")
    unmatched = pd.concat([no_proc.assign(사유="공정정보 없음"), no_p], ignore_index=True)
    unmatched = unmatched[[c for c in cols if c in unmatched] + ["사유"]]
    df = merged[merged["pressure"].notna()].copy()
    df["eqp_mean"] = df.groupby("eqp")["thickness"].transform("mean")
    df["thickness_dev"] = df["thickness"] - df["eqp_mean"]
    print(f"  매칭 {len(df)} / 대기압 없음 {len(no_p)} (허용 ±{MATCH_TOLERANCE_MIN}분)")
    if df.empty:
        raise SystemExit("[오류] 대기압이 매칭된 웨이퍼가 없습니다. 두 데이터의 기간을 확인하세요.")

    print("[4/5] 분석")
    summary = summary_table(df)
    bins = bin_table(df)
    lag = lag_table(df, pressure)

    print("[5/5] 결과 저장")
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
    main()

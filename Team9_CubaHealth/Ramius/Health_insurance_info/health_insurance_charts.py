#!/usr/bin/env python3
"""
health_insurance_charts.py
==========================
Clean and chart U.S. Census ACS 5-year health-insurance tables.

Supported tables (any mix of zips, any number of years / places)
    S2703  Private health insurance coverage by type and selected characteristics
    S2704  Public health insurance coverage by type and selected characteristics
    B27001 Health insurance coverage status by sex by age
    B27011 Health insurance coverage status and type by employment status (ages 19-64)

What it does
------------
1. Reads the "productDownload_*.zip" files straight from the zips (no need to
   extract them). Repeat downloads of the same table/year are ignored.
2. Cleans the very wide Census layout into ONE tidy, sorted table: one row per
   geography x table x year x category, with count, percent, and margin of
   error side by side. Census placeholders such as "(X)" become empty values.
3. Writes that table to  <out>/health_insurance_tidy.csv
4. Draws up to seven figures (PNG files in <out>), depending on which tables
   you supplied:
     1. Coverage by type (latest year)            S2703 + S2704
     2. Coverage trends over time                 S2703 + S2704
     3. Poverty status and employment             S2703 + S2704
     4. Private vs public coverage by age group   S2703 + S2704
     5. Uninsured rate by age group               B27001
     6. Uninsured rate over time (all / male / female)   B27001
     7. Private, public and no coverage by employment status (19-64)   B27011

Usage
-----
    python health_insurance_charts.py                      # reads *.zip in the current folder
    python health_insurance_charts.py path/to/folder       # or a folder of zips
    python health_insurance_charts.py a.zip b.zip          # or specific zip files
    python health_insurance_charts.py data --year 2023     # chart a different year
    python health_insurance_charts.py data --no-show       # save PNGs only (no window)

Requirements:  Python 3.9+,  pandas,  matplotlib,  numpy
    pip install pandas matplotlib numpy

Reading the charts
------------------
* Error bars / bands are the Census 90% margin of error, clipped to 0-100%.
* Hatched (faded) bars are "imprecise": margin of error >= 25 percentage points
  or >= the estimate itself. Treat those as rough guesses, not facts.
* Private and public coverage overlap (a person can have both), so they are
  drawn side by side and never stacked to 100%.
* Each ACS 5-year release covers five years (e.g. 2024 = 2020-2024), so
  neighbouring releases share most of their data.

Where the numbers come from (important for B27001 / B27011)
-----------------------------------------------------------
S2703 / S2704 are "subject tables": Census publishes the percents and their
margins of error, and the script uses those as-is (pct_source = "published").

B27001 / B27011 are "detailed tables": Census publishes COUNTS only. The script
computes the percents itself (pct_source = "computed"):
  * Percent margin of error uses the formula in the Census ACS handbook for a
    share of a group (x out of N):
        MOE% = 100 * sqrt(MOE_x^2 - (x/N * MOE_N)^2) / N
    falling back to a "+" under the root if that value would be negative.
    It is an approximation. Checked against the percent margins Census itself
    publishes in S2703/S2704 for this same village (236 comparable rows) it was
    within 1.5x of the published margin in 84% of rows, but in about 1 row in 9
    it was more than 1.5x too narrow. Treat computed margins as a guide.
  * --conservative-moe switches to the ratio formula (always wider). That is
    safer against false confidence but on average 1.6x Census's own margins,
    and about 4x for shares above 75%, so it can hide real differences.
  * Where counts are added together (e.g. male + female, or all age bands)
    the rows are marked derived = True and the margins of error are combined
    as the square root of the sum of squares (the Census-recommended method;
    when several estimates are zero only the largest zero's margin is used).
For tighter published percents, download table S2701 as well.
"""
from __future__ import annotations

import argparse
import posixpath
import re
import sys
import textwrap
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from matplotlib.colors import to_rgba
from matplotlib.patches import Patch

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
SUBJECT_TABLES = {"S2703": "Private", "S2704": "Public"}  # table -> coverage group
DETAIL_TABLES = {"B27001", "B27011"}
SUPPORTED_TABLES = set(SUBJECT_TABLES) | DETAIL_TABLES

COLORS = {  # colour-blind friendly (Okabe-Ito)
    "Private": "#0072B2",
    "Public": "#D55E00",
    "Uninsured": "#CC79A7",
}
LEGEND_LABELS = {"Private": "Private coverage", "Public": "Public coverage", "Uninsured": "No health insurance"}
WIDE_MOE_PTS = 25.0  # margin of error (in percentage points) that counts as "imprecise"

# dimension names (the "dimension" column of the tidy table)
DIM_TOTAL = "Total"
DIM_COMBO = "Coverage type (alone or in combination)"
DIM_ALONE = "Coverage type (alone only)"
DIM_POVERTY = "Poverty status"
DIM_WORK = "Employment"
DIM_AGE = "Age group"
DIM_ALL = "All residents"
DIM_AGE_ALL = "Age group (all sexes)"
DIM_SEX = "Sex (all ages)"
DIM_SEXAGE = "Sex by age group"
DIM_LF = "Labor force status (ages 19-64)"

# Order of bars / panels in figures 1 and 2
COVERAGE_LAYOUT = [
    ("Private", ["Any private coverage", "Employer-based", "Direct-purchase", "Tricare/military"]),
    ("Public", ["Any public coverage", "Medicare", "Medicaid/means-tested", "VA"]),
]

# ACSST5Y = subject tables, ACSDT5Y = detailed tables
FILE_RE = re.compile(
    r"ACS(?:ST|DT)5Y(?P<year>\d{4})\.(?P<table>[A-Z]\d{4,5})-(?P<kind>Data|Column-Metadata)\.csv", re.I
)
COL_RE = re.compile(r"^(?P<table>S\d{4})_(?P<grp>C\d{2})_(?P<row>\d{3})(?P<kind>[EM])$")
# Subject tables: C01 = total people in the row's group, C02 = number covered, C03 = percent covered
MEASURE = {"C01": "universe", "C02": "covered", "C03": "pct_covered"}

# Detailed tables: coverage statuses as they appear in the Census labels
STATUS = {
    "with health insurance coverage": "Insured",
    "no health insurance coverage": "Uninsured",
    "with private health insurance": "Private",
    "with public coverage": "Public",
}
STATUS_LABEL = {
    "Insured": "With health insurance coverage",
    "Uninsured": "No health insurance coverage",
    "Private": "With private health insurance",
    "Public": "With public coverage",
}

FOOTER_S = (
    "Source: U.S. Census Bureau, American Community Survey 5-year estimates, "
    "tables S2703 (private) and S2704 (public).\n"
    "Error bars / bands show the 90% margin of error (clipped to 0-100%). "
    "Overlapping 5-year windows mean neighbouring years are not independent."
)
FOOTER_B = (
    "Source: U.S. Census Bureau, American Community Survey 5-year estimates, detailed tables "
    "B27001 (sex by age) and B27011 (employment status, ages 19-64).\n"
    "Census publishes counts only for these tables, so percents and margins of error are computed here "
    "(approximation; sums use root-sum-of-squares).\n"
    "Error bars / bands show the 90% margin of error (clipped to 0-100%). "
    "Overlapping 5-year windows mean neighbouring years are not independent."
)


# --------------------------------------------------------------------------- #
# Small numeric helpers
# --------------------------------------------------------------------------- #
def to_number(value) -> float:
    """Convert a Census cell to a float; placeholders like '(X)', '-', '**', 'N' -> NaN."""
    if value is None:
        return np.nan
    text = str(value).strip().replace(",", "")
    match = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)[+-]?", text)  # tolerates '250,000+' / '2,500-'
    return float(match.group(1)) if match else np.nan


def sum_est_moe(est, moe) -> tuple[float, float]:
    """Add estimates; combine margins of error as sqrt(sum of squares).

    Census rule: when several of the added estimates are zero, only the largest
    of their margins of error is included. Any missing input -> missing result.
    """
    est = np.asarray(est, dtype=float)
    moe = np.asarray(moe, dtype=float)
    if est.size == 0 or np.isnan(est).any() or np.isnan(moe).any():
        return np.nan, np.nan
    zero = est == 0
    squares = float((moe[~zero] ** 2).sum())
    if zero.any():
        squares += float(moe[zero].max() ** 2)
    return float(est.sum()), float(np.sqrt(squares))


def pct_with_moe(x, moe_x, n, moe_n, conservative: bool = False) -> tuple[float, float]:
    """Percent of a group (x out of n) and its 90% margin of error, in percentage points.

    Default: the Census handbook formula for a proportion (ratio formula if the value under the
    root would be negative). conservative=True always uses the wider ratio formula.
    """
    if any(np.isnan(v) for v in (x, moe_x, n, moe_n)) or n <= 0:
        return np.nan, np.nan
    p = x / n
    radicand = moe_x**2 + (p * moe_n) ** 2 if conservative else moe_x**2 - (p * moe_n) ** 2
    if radicand < 0:
        radicand = moe_x**2 + (p * moe_n) ** 2
    return round(100.0 * p, 1), round(float(100.0 * np.sqrt(radicand) / n), 1)


# --------------------------------------------------------------------------- #
# Label cleaning (shared)
# --------------------------------------------------------------------------- #
def short_type(text: str) -> str:
    """Normalise a coverage-type label ('Tricare/military  health insurance ...')."""
    s = re.sub(r"\s+", " ", text).strip().lower()
    for key, name in (
        ("employer", "Employer-based"),
        ("direct-purchase", "Direct-purchase"),
        ("tricare", "Tricare/military"),
        ("medicare", "Medicare"),
        ("medicaid", "Medicaid/means-tested"),
        ("subsidized", "Subsidized marketplace"),
    ):
        if key in s:
            return name
    if s.startswith("va "):
        return "VA"
    if s.startswith("private"):
        return "Private"
    if s.startswith("public"):
        return "Public"
    return re.sub(r" alone( or in combination)?$", "", re.sub(r"\s+", " ", text).strip())


def clean_subgroup(text: str) -> str:
    """'19 to 25 years' -> '19-25', '75 years and over' -> '75+', 'Under 6 years' -> 'Under 6'."""
    s = re.sub(r"\s+", " ", text).strip()
    s = s.replace("Worked full-time, year-round (19-64 years)", "Full-time, year-round workers (19\u201364)")
    s = re.sub(r"(\d+) to (\d+) years", "\\1\u2013\\2", s)
    s = re.sub(r"(\d+) years and over", "\\1+", s)
    s = re.sub(r"^Under (\d+) years$", "Under \\1", s)
    s = re.sub(r"(\d+) percent of the poverty threshold", "\\1% of poverty line", s)
    return s.replace("At or above", "At/above")


# --------------------------------------------------------------------------- #
# Subject tables (S2703 / S2704): Census publishes counts AND percents
# --------------------------------------------------------------------------- #
def classify(rest: list[str], group: str) -> tuple[str, str, str]:
    """Turn the label path of one subject-table row into (dimension, category, subgroup)."""
    if not rest:
        return DIM_TOTAL, f"Any {group.lower()} coverage", "All ages"
    section = re.sub(r"\s+", " ", rest[0]).strip().upper()
    item = rest[1] if len(rest) > 1 else ""
    sub = rest[2] if len(rest) > 2 else None

    if section == "COVERAGE ALONE OR IN COMBINATION":
        return DIM_COMBO, short_type(item), clean_subgroup(sub) if sub else "All ages"
    if section == "COVERAGE ALONE":
        return DIM_ALONE, f"{short_type(sub if sub else item)} only", "All ages"
    if section.endswith("HEALTH INSURANCE ALONE OR IN COMBINATION"):  # PRIVATE / PUBLIC ...
        low = item.lower()
        if "poverty" in low:
            dim = DIM_POVERTY
        elif "worked" in low or "full-time" in low:
            dim = DIM_WORK
        elif re.search(r"\d|under", low):
            dim = DIM_AGE
        else:
            dim = "Other"
        return dim, f"Any {group.lower()} coverage", clean_subgroup(item)
    return "Other", short_type(item or section), clean_subgroup(sub or item or section)


def tidy_subject_year(table: str, year: int, data: pd.DataFrame, meta_df: pd.DataFrame) -> pd.DataFrame:
    """Reshape one subject table/year (wide) into tidy rows (one per geography x table row)."""
    group = SUBJECT_TABLES[table]
    meta = dict(zip(meta_df["Column Name"], meta_df["Label"]))

    cols = [c for c in data.columns if COL_RE.match(c)]
    long = data.melt(id_vars=["GEO_ID", "NAME"], value_vars=cols, var_name="col", value_name="raw")
    pieces = long["col"].str.extract(COL_RE)
    long["row_id"] = pieces["row"].astype(int)
    long["measure"] = [MEASURE[g] + ("_moe" if k == "M" else "") for g, k in zip(pieces["grp"], pieces["kind"])]
    long["value"] = long["raw"].map(to_number)

    wide = long.pivot(index=["GEO_ID", "NAME", "row_id"], columns="measure", values="value")
    wide = wide.reindex(
        columns=["universe", "universe_moe", "covered", "covered_moe", "pct_covered", "pct_covered_moe"]
    ).reset_index()
    wide.columns.name = None

    # Where the Census prints "(X)" for the group size, the percent is of the whole
    # civilian noninstitutionalized population (row 001). Fill that in and flag it.
    totals = wide[wide["row_id"] == 1].set_index("GEO_ID")
    missing = wide["universe"].isna() & wide["covered"].notna()
    wide["universe_is_total_pop"] = missing
    wide.loc[missing, "universe"] = wide.loc[missing, "GEO_ID"].map(totals["universe"])
    wide.loc[missing, "universe_moe"] = wide.loc[missing, "GEO_ID"].map(totals["universe_moe"])

    # Drop rows with no data at all
    wide = wide.dropna(subset=["covered", "pct_covered"], how="all").copy()

    # Describe each row (dimension / category / subgroup) from the metadata labels
    descriptions = []
    for rid in sorted(wide["row_id"].unique()):
        label = meta.get(f"{table}_C01_{rid:03d}E") or meta.get(f"{table}_C02_{rid:03d}E") or ""
        # Label = "Estimate!!Total!![Civilian noninstitutionalized population!!]SECTION!!item!!subitem".
        # S2703 includes the "Civilian ..." segment on every row, S2704 does not -> handle both.
        segs = [s.strip() for s in label.split("!!")][2:]
        if segs and segs[0].lower().startswith("civilian noninstitutionalized"):
            segs = segs[1:]
        dim, cat, sub = classify(segs, group)
        descriptions.append(
            {
                "row_id": rid,
                "dimension": dim,
                "category": cat,
                "subgroup": sub,
                "source_label": " | ".join(segs) if segs else "Total civilian noninstitutionalized population",
            }
        )
    out = wide.merge(pd.DataFrame(descriptions), on="row_id", how="left")

    out["table_id"] = table
    out["coverage_group"] = group
    out["year"] = year
    out["acs_period"] = f"{year - 4}\u2013{year}"
    out["derived"] = False
    out["pct_source"] = "published"
    return out.rename(columns={"GEO_ID": "geo_id", "NAME": "geography"})


# --------------------------------------------------------------------------- #
# Detailed tables (B27001 / B27011): Census publishes COUNTS only
# --------------------------------------------------------------------------- #
def _emit(rows: list, ctx: dict, dimension: str, subgroup: str, status: str,
          covered: tuple, universe: tuple, derived: bool, source: str) -> None:
    """Append one tidy row; the percent and its margin of error are computed from the counts."""
    pct, pct_moe = pct_with_moe(covered[0], covered[1], universe[0], universe[1], ctx["conservative"])
    rows.append(
        {
            "geo_id": ctx["geo_id"], "geography": ctx["geography"], "table_id": ctx["table"],
            "coverage_group": status, "year": ctx["year"], "acs_period": f"{ctx['year'] - 4}\u2013{ctx['year']}",
            "row_id": 0,  # assigned later so ids are identical in every year
            "dimension": dimension, "category": STATUS_LABEL[status], "subgroup": subgroup,
            "universe": universe[0], "universe_moe": universe[1],
            "covered": covered[0], "covered_moe": covered[1],
            "pct_covered": pct, "pct_covered_moe": pct_moe,
            "universe_is_total_pop": False, "source_label": source,
            "derived": derived, "pct_source": "computed (conservative)" if ctx["conservative"] else "computed",
        }
    )


def _sum_cells(members: list, cells: dict, statuses) -> dict:
    out = {}
    for st in statuses:
        pairs = [cells[p][st] for p in members if st in cells[p]]
        out[st] = sum_est_moe([a for a, _ in pairs], [b for _, b in pairs])
    return out


def _sum_universe(members: list, uni: dict) -> tuple:
    pairs = [uni.get(p, (np.nan, np.nan)) for p in members]
    return sum_est_moe([a for a, _ in pairs], [b for _, b in pairs])


def _published_or_sum(key: tuple, members: list, uni: dict) -> tuple:
    """Use Census's own total for a group when the table has one; otherwise add up its parts."""
    pub = uni.get(key)
    if pub is not None and not np.isnan(pub[0]):
        return pub
    return _sum_universe(members, uni)


def _rows_sex_by_age(ctx: dict, cells: dict, uni: dict, rows: list) -> None:
    """B27001: insured / uninsured by sex and age -> all residents, by age, by sex, by sex x age."""
    statuses = ("Insured", "Uninsured")
    groups = [p for p in cells if len(p) == 2]  # (sex, age), in table order
    sexes = list(dict.fromkeys(p[0] for p in groups))
    ages = list(dict.fromkeys(p[1] for p in groups))

    # check: insured + uninsured should equal the group size (counts are rounded -> allow 1)
    for p in groups:
        c, u = cells[p], uni.get(p, (np.nan, np.nan))
        if "Insured" in c and "Uninsured" in c and not np.isnan(u[0]):
            if abs(c["Insured"][0] + c["Uninsured"][0] - u[0]) > 1:
                ctx["issues"].append(f"{' / '.join(p)}: insured + uninsured != group size")

    def emit_group(dimension, subgroup, members, universe, derived, source):
        sums = _sum_cells(members, cells, statuses) if derived else {s: cells[members[0]].get(s, (np.nan, np.nan)) for s in statuses}
        for st in statuses:
            _emit(rows, ctx, dimension, subgroup, st, sums[st], universe, derived, f"{source} | {STATUS_LABEL[st]}")

    emit_group(DIM_ALL, "All ages", groups, _published_or_sum((), groups, uni), True,
               "Sum of published cells: all sexes and ages")
    for age in ages:
        members = [p for p in groups if p[1] == age]
        emit_group(DIM_AGE_ALL, clean_subgroup(age), members, _sum_universe(members, uni), True,
                   f"Sum of published cells: {age}, all sexes")
    for sex in sexes:
        members = [p for p in groups if p[0] == sex]
        emit_group(DIM_SEX, sex, members, _published_or_sum((sex,), members, uni), True,
                   f"Sum of published cells: {sex}, all ages")
    for p in groups:
        emit_group(DIM_SEXAGE, f"{p[0]}, {clean_subgroup(p[1])}", [p], uni.get(p, (np.nan, np.nan)), False,
                   " | ".join(p))


def _rows_labor_force(ctx: dict, cells: dict, uni: dict, rows: list) -> None:
    """B27011: insured / uninsured / private / public by employment status (ages 19-64)."""
    statuses = ("Insured", "Uninsured", "Private", "Public")
    groups = list(cells)  # e.g. ('In labor force', 'Employed'), ('Not in labor force',)

    for p in groups:
        c, u = cells[p], uni.get(p, (np.nan, np.nan))
        if np.isnan(u[0]):
            continue
        if "Insured" in c and "Uninsured" in c and abs(c["Insured"][0] + c["Uninsured"][0] - u[0]) > 1:
            ctx["issues"].append(f"{p[-1]}: insured + uninsured != group size")
        for st in ("Private", "Public"):
            if st in c and "Insured" in c and c[st][0] > c["Insured"][0] + 1:
                ctx["issues"].append(f"{p[-1]}: {st.lower()} count exceeds insured count")

    sums = _sum_cells(groups, cells, statuses)
    all_universe = _published_or_sum((), groups, uni)
    for st in statuses:
        _emit(rows, ctx, DIM_LF, "All adults 19-64", st, sums[st], all_universe, True,
              f"Sum of published cells: all employment groups | {STATUS_LABEL[st]}")
    for p in groups:
        for st in statuses:
            if st in cells[p]:
                _emit(rows, ctx, DIM_LF, p[-1], st, cells[p][st], uni.get(p, (np.nan, np.nan)), False,
                      " | ".join(p) + f" | {STATUS_LABEL[st]}")


DETAIL_BUILDERS = {"B27001": _rows_sex_by_age, "B27011": _rows_labor_force}


def tidy_detailed_year(table: str, year: int, data: pd.DataFrame, meta_df: pd.DataFrame,
                       conservative: bool = False):
    """Reshape one detailed table/year into tidy rows. Returns (DataFrame, list of issue strings)."""
    meta = dict(zip(meta_df["Column Name"], meta_df["Label"]))

    # cell number -> label path, e.g. '004' -> ('Male', 'Under 6 years', 'With health insurance coverage')
    paths = {}
    for code, label in meta.items():
        m = re.fullmatch(rf"{table}_(\d{{3}})E", code)
        if m:
            segs = [s.strip().rstrip(":").strip() for s in label.split("!!")][2:]  # drop "Estimate", "Total"
            paths[m.group(1)] = tuple(segs)

    frames, issues = [], []
    for _, r in data.iterrows():
        cells: dict = {}  # group path -> {status: (estimate, moe)}
        uni: dict = {}    # group path -> (estimate, moe) of the group total
        for num in sorted(paths):
            path = paths[num]
            pair = (to_number(r.get(f"{table}_{num}E")), to_number(r.get(f"{table}_{num}M")))
            idx = next((i for i, s in enumerate(path) if s.lower() in STATUS), None)
            if idx is None:
                uni[path] = pair
            else:
                cells.setdefault(path[:idx], {})[STATUS[path[-1].lower()]] = pair

        ctx = {"geo_id": r["GEO_ID"], "geography": r["NAME"], "table": table, "year": year, "issues": [],
               "conservative": conservative}
        rows: list = []
        DETAIL_BUILDERS[table](ctx, cells, uni, rows)
        frames.append(pd.DataFrame(rows))
        issues += [f"{table} {year}, {ctx['geography']}: {msg}" for msg in ctx["issues"]]
    return pd.concat(frames, ignore_index=True), issues


# --------------------------------------------------------------------------- #
# Reading zips and assembling the tidy table
# --------------------------------------------------------------------------- #
def read_zip_into(path: Path, parts: dict, notes: list[str], info: dict) -> None:
    """Read Data / Column-Metadata CSVs from one zip into `parts[(table, year)]`."""
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            base = posixpath.basename(name)
            if base.startswith("._"):  # macOS resource-fork junk
                continue
            m = FILE_RE.fullmatch(base)
            if not m:
                continue
            table, year, kind = m["table"].upper(), int(m["year"]), m["kind"].lower()
            if table not in SUPPORTED_TABLES:
                info["skipped"].add(table)
                continue
            with zf.open(name) as fh:
                if kind == "data":
                    # row 0 = column codes, row 1 = long labels -> skip the labels row
                    df = pd.read_csv(fh, skiprows=[1], dtype=str, keep_default_na=False, encoding="utf-8-sig")
                else:
                    df = pd.read_csv(fh, dtype=str, keep_default_na=False, encoding="utf-8-sig")
            slot = parts.setdefault((table, year), {})
            if kind in slot:
                if slot[kind].equals(df):
                    info["dupes"].add(path.name)
                else:
                    notes.append(f"WARNING: {table} {year} {kind} in {path.name} DIFFERS from an earlier copy "
                                 "- kept the first one")
                continue
            slot[kind] = df


def add_quality_columns(tidy: pd.DataFrame) -> pd.DataFrame:
    """Add integrity and reliability flags."""
    u, c, p = tidy["universe"], tidy["covered"], tidy["pct_covered"]
    with np.errstate(divide="ignore", invalid="ignore"):
        calc = (100 * c / u).replace([np.inf, -np.inf], np.nan)
        # counts are rounded to whole people, so allow for that rounding in the comparison
        tol = 100 * ((c + 0.5) / (u - 0.5).clip(lower=0.5) - c / u) + 0.06
    # only meaningful where Census published the percent (computed percents match by construction)
    comparable = calc.notna() & p.notna() & (tidy["pct_source"] == "published")
    ok = ((calc - p).abs() <= tol).astype("boolean")
    tidy["pct_matches_counts"] = ok.mask(~comparable)

    moe, est = tidy["pct_covered_moe"], tidy["pct_covered"]
    tidy["low_reliability"] = ((moe >= WIDE_MOE_PTS) | ((moe >= est) & (est > 0))).astype(bool)
    return tidy


def cross_checks(tidy: pd.DataFrame, notes: list[str]) -> None:
    """Detailed-table totals should equal the matching population in S2703 (same survey, same people)."""
    s = tidy[tidy["table_id"] == "S2703"]
    checks = [
        ("B27001", DIM_ALL, "All ages", s[s["row_id"] == 1]),
        ("B27011", DIM_LF, "All adults 19-64",
         s[(s["dimension"] == DIM_COMBO) & (s["category"] == "Employer-based") & (s["subgroup"] == "19\u201364")]),
    ]
    for table, dim, sub, ref in checks:
        b = tidy[(tidy["table_id"] == table) & (tidy["dimension"] == dim) & (tidy["subgroup"] == sub)]
        if b.empty or ref.empty:
            continue
        b = b.drop_duplicates(["geo_id", "year"])[["geo_id", "year", "universe"]]
        ref = ref.drop_duplicates(["geo_id", "year"])[["geo_id", "year", "universe"]]
        both = b.merge(ref, on=["geo_id", "year"], suffixes=("_detail", "_subject"))
        if both.empty:
            continue
        match = int((both["universe_detail"] == both["universe_subject"]).sum())
        label = f"Cross-check {table} vs S2703 population: matches in {match} of {len(both)} geography-year(s)"
        notes.append(label if match == len(both) else "WARNING: " + label)


def build_tidy(zip_paths: list[Path], conservative_moe: bool = False) -> tuple[pd.DataFrame, list[str]]:
    parts: dict = {}
    notes: list[str] = []
    info = {"dupes": set(), "skipped": set()}
    for path in zip_paths:
        try:
            read_zip_into(path, parts, notes, info)
        except zipfile.BadZipFile:
            notes.append(f"Skipped {path.name}: not a valid zip file")
    if info["dupes"]:
        notes.append("Ignored repeat downloads with identical content: " + ", ".join(sorted(info["dupes"])))
    if info["skipped"]:
        notes.append("Skipped unsupported table(s): " + ", ".join(sorted(info["skipped"]))
                     + f" (supported: {', '.join(sorted(SUPPORTED_TABLES))})")

    frames = []
    for (table, year), slot in sorted(parts.items()):
        if "data" not in slot or "column-metadata" not in slot:
            notes.append(f"Skipped {table} {year}: need both the -Data.csv and -Column-Metadata.csv files")
            continue
        if table in SUBJECT_TABLES:
            frames.append(tidy_subject_year(table, year, slot["data"], slot["column-metadata"]))
        else:
            frame, issues = tidy_detailed_year(table, year, slot["data"], slot["column-metadata"], conservative_moe)
            frames.append(frame)
            notes += [f"WARNING: {msg}" for msg in issues]
    if not frames:
        raise SystemExit("No usable S2703 / S2704 / B27001 / B27011 Data + Column-Metadata files were found.")

    tidy = pd.concat(frames, ignore_index=True)

    # Detailed tables have no published row numbers for our derived rows: number them by first
    # appearance so every year uses the same row_id for the same row.
    for table in DETAIL_TABLES:
        mask = tidy["table_id"] == table
        if mask.any():
            keys = pd.Series(list(zip(tidy.loc[mask, "dimension"], tidy.loc[mask, "subgroup"],
                                      tidy.loc[mask, "coverage_group"])), index=tidy.index[mask])
            tidy.loc[mask, "row_id"] = pd.factorize(keys)[0] + 1

    tidy = add_quality_columns(tidy)

    # Row definitions should not change between years
    key = tidy["dimension"] + "|" + tidy["category"] + "|" + tidy["subgroup"]
    changed = key.groupby([tidy["table_id"], tidy["row_id"]]).nunique()
    for (table, rid), n in changed[changed > 1].items():
        notes.append(f"WARNING: {table} row {rid:03d} is defined differently in different years")

    unclassified = tidy[tidy["dimension"] == "Other"]
    if len(unclassified):
        notes.append(f"WARNING: {len(unclassified)} row(s) could not be classified (dimension 'Other'); "
                     "charts may be missing data. Example: " + str(unclassified.iloc[0]["source_label"]))

    cross_checks(tidy, notes)

    for col in ["universe", "universe_moe", "covered", "covered_moe"]:
        tidy[col] = tidy[col].round().astype("Int64")

    tidy = tidy.sort_values(["geography", "table_id", "row_id", "year"]).reset_index(drop=True)
    ordered = [
        "geo_id", "geography", "table_id", "coverage_group", "year", "acs_period", "row_id",
        "dimension", "category", "subgroup",
        "universe", "universe_moe", "covered", "covered_moe", "pct_covered", "pct_covered_moe",
        "low_reliability", "pct_source", "derived", "universe_is_total_pop", "pct_matches_counts",
        "source_label",
    ]
    return tidy[ordered], notes


# --------------------------------------------------------------------------- #
# Plot helpers
# --------------------------------------------------------------------------- #
def err_lengths(est, moe):
    """Asymmetric error lengths (lower, upper), clipped so bars stay within 0-100%."""
    est = np.asarray(est, dtype=float)
    moe = np.asarray(moe, dtype=float)
    lower = np.clip(np.minimum(moe, est), 0, None)
    upper = np.clip(np.minimum(moe, 100 - est), 0, None)
    return lower, upper


def bar_style(color: str, imprecise: bool) -> dict:
    if imprecise:
        return dict(facecolor=to_rgba(color, 0.30), edgecolor=color, hatch="///", linewidth=1.0)
    return dict(facecolor=to_rgba(color, 0.90), edgecolor=color, linewidth=1.0)


def legend_handles(groups=("Private", "Public")) -> list:
    handles = [
        Patch(facecolor=to_rgba(COLORS[g], 0.9), edgecolor=COLORS[g], label=LEGEND_LABELS[g]) for g in groups
    ]
    handles.append(
        Patch(
            facecolor="white", edgecolor="#555555", hatch="///",
            label=f"Imprecise estimate (margin of error \u2265 {WIDE_MOE_PTS:.0f} pts or \u2265 the estimate)",
        )
    )
    return handles


def finish(fig, handles=None, footer: str = FOOTER_S) -> None:
    lines = footer.count("\n") + 1
    if handles:
        fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False,
                   bbox_to_anchor=(0.5, 0.011 + 0.022 * lines), fontsize=9)
    fig.text(0.012, 0.012, footer, fontsize=7.5, color="#555555", ha="left", va="bottom")
    bottom = 0.026 + 0.022 * lines + (0.05 if handles else 0.0)
    fig.tight_layout(rect=(0, bottom, 1, 1))


def style_axes(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_axisbelow(True)


def coverage_frame(d: pd.DataFrame) -> pd.DataFrame:
    """Rows used by figures 1 and 2: 'any private/public' plus each coverage type, all ages."""
    return d[(d["dimension"] == DIM_TOTAL) | ((d["dimension"] == DIM_COMBO) & (d["subgroup"] == "All ages"))]


def draw_band_line(ax, s: pd.DataFrame, color: str) -> None:
    """Line of estimates with a shaded 90% margin-of-error band."""
    x = s["year"].to_numpy(dtype=float)
    est = s["pct_covered"].to_numpy(dtype=float)
    moe = s["pct_covered_moe"].to_numpy(dtype=float)
    ax.fill_between(x, np.clip(est - moe, 0, None), np.clip(est + moe, None, 100),
                    color=color, alpha=0.20, linewidth=0)
    ax.plot(x, est, marker="o", color=color, linewidth=2)


def set_year_axis(axes, years: list[int]) -> None:
    for ax in np.ravel(axes):
        ax.set_xticks(years)
        ax.set_xticklabels([f"{y}\n({y - 4}\u2013{str(y)[2:]})" for y in years], fontsize=8)
        ax.set_ylim(0, 100)
        ax.yaxis.set_major_formatter(mticker.PercentFormatter(100, decimals=0))


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #
def chart_coverage_by_type(d: pd.DataFrame, geo: str, year: int):
    frame = coverage_frame(d[d["year"] == year])
    labels, ypos, rows = [], [], []
    y = 0.0
    for group, cats in COVERAGE_LAYOUT:
        for cat in cats:
            r = frame[(frame["coverage_group"] == group) & (frame["category"] == cat)]
            if r.empty:
                continue
            labels.append(cat)
            ypos.append(y)
            rows.append(r.iloc[0])
            y += 1.0
        y += 0.6  # gap between the private and public blocks
    if not rows:
        return None

    fig, ax = plt.subplots(figsize=(10, 6.2))
    for y, r in zip(ypos, rows):
        est, moe = r["pct_covered"], r["pct_covered_moe"]
        ax.barh(y, est, height=0.7, **bar_style(COLORS[r["coverage_group"]], bool(r["low_reliability"])))
        lo, hi = err_lengths([est], [moe])
        ax.errorbar(est, y, xerr=[lo, hi], fmt="none", ecolor="black", elinewidth=1, capsize=3)
        ax.text(1.01, y, f"{est:.1f}%  \u00b1 {moe:.1f}", transform=ax.get_yaxis_transform(),
                va="center", fontsize=9)

    ax.set_yticks(ypos)
    ax.set_yticklabels(labels)
    for tick in ax.get_yticklabels():
        if tick.get_text().startswith("Any"):
            tick.set_fontweight("bold")
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(100, decimals=0))
    ax.grid(axis="x", alpha=0.3)
    style_axes(ax)
    ax.set_xlabel("Share of residents covered (people can hold more than one type)")
    ax.set_title(f"Health insurance coverage by type, {year}\n{geo}  (ACS {year - 4}\u2013{year})", loc="left")
    finish(fig, legend_handles())
    return fig


def chart_trends(d: pd.DataFrame, geo: str):
    frame = coverage_frame(d)
    years = sorted(int(v) for v in frame["year"].unique())
    fig, axes = plt.subplots(2, 4, figsize=(15, 7.4), sharex=True, sharey=True)
    for r, (group, cats) in enumerate(COVERAGE_LAYOUT):
        for c, cat in enumerate(cats):
            ax = axes[r][c]
            ax.set_title(cat, fontsize=10, fontweight="bold" if cat.startswith("Any") else "normal")
            s = frame[(frame["coverage_group"] == group) & (frame["category"] == cat)].sort_values("year")
            style_axes(ax)
            ax.grid(axis="y", alpha=0.3)
            if not s.empty:
                draw_band_line(ax, s, COLORS[group])
        axes[r][0].set_ylabel(f"{group} coverage")
    set_year_axis(axes, years)
    fig.suptitle(f"Coverage over time \u2013 {geo}\n"
                 "Line = estimate, shaded band = 90% margin of error (x-axis: ACS 5-year release and the years it covers)",
                 x=0.012, ha="left", fontsize=11)
    finish(fig)
    fig.subplots_adjust(top=0.86)
    return fig


def chart_uninsured_trend(d: pd.DataFrame, geo: str):
    frame = d[(d["coverage_group"] == "Uninsured") & d["dimension"].isin([DIM_ALL, DIM_SEX])]
    if frame.empty:
        return None
    years = sorted(int(v) for v in frame["year"].unique())
    panels = [("All residents", DIM_ALL, "All ages"), ("Male", DIM_SEX, "Male"), ("Female", DIM_SEX, "Female")]
    fig, axes = plt.subplots(1, 3, figsize=(13, 5.4), sharex=True, sharey=True)
    for ax, (title, dim, sub) in zip(axes, panels):
        s = frame[(frame["dimension"] == dim) & (frame["subgroup"] == sub)].sort_values("year")
        ax.set_title(title, fontsize=10, fontweight="bold" if dim == DIM_ALL else "normal")
        style_axes(ax)
        ax.grid(axis="y", alpha=0.3)
        if not s.empty:
            draw_band_line(ax, s, COLORS["Uninsured"])
    axes[0].set_ylabel("Share with no health insurance")
    set_year_axis(axes, years)
    upper = (frame["pct_covered"] + frame["pct_covered_moe"]).clip(upper=100).max()
    top = 100 if np.isnan(upper) else min(100, max(10, int(np.ceil((upper + 3) / 10.0)) * 10))
    for ax in axes:
        ax.set_ylim(0, top)
    fig.suptitle(f"Uninsured rate over time \u2013 {geo}\n"
                 "Line = estimate, shaded band = 90% margin of error (x-axis: ACS 5-year release and the years it covers)",
                 x=0.012, ha="left", fontsize=11)
    finish(fig, footer=FOOTER_B)
    fig.subplots_adjust(top=0.84)
    return fig


def chart_grouped(d: pd.DataFrame, geo: str, year: int, dims: list[str], title: str,
                  figsize: tuple, wrap: int, groups=("Private", "Public"),
                  ylabel: str = "Share of the group covered", footer: str = FOOTER_S):
    sub = d[(d["year"] == year) & d["dimension"].isin(dims)]
    if sub.empty:
        return None
    order = sub.sort_values("row_id").drop_duplicates("subgroup")["subgroup"].tolist()
    x = np.arange(len(order))
    n = len(groups)
    width = 0.76 / n
    n_missing = 0

    fig, ax = plt.subplots(figsize=figsize)
    for k, group in enumerate(groups):
        offset = (k - (n - 1) / 2) * width
        for i, name in enumerate(order):
            r = sub[(sub["coverage_group"] == group) & (sub["subgroup"] == name)]
            if r.empty:
                continue
            r = r.iloc[0]
            xi = x[i] + offset
            if pd.isna(r["pct_covered"]):  # e.g. a group whose estimated size is 0
                ax.text(xi, 2, "n/a", ha="center", va="bottom", fontsize=8, color="#666666")
                n_missing += 1
                continue
            est, moe = float(r["pct_covered"]), r["pct_covered_moe"]
            ax.bar(xi, est, width * 0.95, **bar_style(COLORS[group], bool(r["low_reliability"])))
            lo, hi = err_lengths([est], [moe])
            ax.errorbar(xi, est, yerr=[lo, hi], fmt="none", ecolor="black", elinewidth=1, capsize=3)
            top = est + (0.0 if np.isnan(hi[0]) else hi[0])
            ax.text(xi, top + 1.5, f"{est:.0f}%", ha="center", va="bottom", fontsize=8.5)

    ax.set_xticks(x)
    ax.set_xticklabels([textwrap.fill(s, wrap, break_on_hyphens=False) for s in order])
    ax.set_xlim(-0.6, len(order) - 0.4)  # fixed range so "n/a" labels never fall outside the axes
    ax.set_ylim(0, 118)
    ax.set_yticks(range(0, 101, 20))
    ax.spines["left"].set_bounds(0, 100)  # headroom above 100% is only for the value labels
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(100, decimals=0))
    ax.grid(axis="y", alpha=0.3)
    style_axes(ax)
    ax.set_ylabel(ylabel)
    ax.set_title(f"{title}, {year}\n{geo}  (ACS {year - 4}\u2013{year})", loc="left")
    if n_missing:
        ax.text(0.99, 0.98, "n/a = no percentage available (group size is 0 or Census withheld a value)", transform=ax.transAxes,
                ha="right", va="top", fontsize=8, color="#666666")
    finish(fig, legend_handles(groups), footer)
    return fig


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def collect_zip_paths(inputs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for item in inputs:
        p = Path(item).expanduser()
        if p.is_dir():
            paths.extend(sorted(p.glob("*.zip")))
        elif p.is_file():
            paths.append(p)
        else:
            print(f"Warning: {item} not found", file=sys.stderr)
    if not paths:
        raise SystemExit("No .zip files found. Pass a folder or zip files as arguments.")
    return paths


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Clean ACS health-insurance tables and chart them.")
    ap.add_argument("inputs", nargs="*", default=["."], help="folder(s) of zips and/or zip files (default: .)")
    ap.add_argument("--out", default="health_insurance_output", help="output folder (default: health_insurance_output)")
    ap.add_argument("--year", type=int, help="ACS 5-year release for the single-year figures (default: latest)")
    ap.add_argument("--no-show", action="store_true", help="save PNGs without opening chart windows")
    ap.add_argument("--conservative-moe", action="store_true",
                    help="B27001/B27011 only: use the wider ratio formula for computed percent margins of error")
    args = ap.parse_args(argv)

    if args.no_show:
        plt.switch_backend("Agg")

    paths = collect_zip_paths(args.inputs)
    tidy, notes = build_tidy(paths, args.conservative_moe)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "health_insurance_tidy.csv"
    tidy.to_csv(csv_path, index=False)

    # ---- cleaning report
    print(f"Read {len(paths)} zip file(s).")
    for n in notes:
        print(f"  note: {n}")
    tables = tidy.groupby("table_id")["year"].agg(lambda s: f"{s.min()}-{s.max()}")
    print("Tables/years: " + ", ".join(f"{t} ({y})" for t, y in tables.items()))
    print(f"Tidy table: {len(tidy)} rows, {tidy['geography'].nunique()} geography(ies) -> {csv_path}")
    mism = int((tidy["pct_matches_counts"] == False).sum())  # noqa: E712  (NA-safe comparison)
    print(f"Integrity check (published percent vs. count/group size): {mism} mismatching row(s)")
    print(f"Imprecise estimates flagged: {int(tidy['low_reliability'].sum())} of {len(tidy)} rows")

    # ---- charts
    def pick_year(years: list[int], label: str):
        if args.year is None:
            return years[-1]
        if args.year in years:
            return args.year
        print(f"  (skipped {label} figures: {args.year} not available; those tables cover {years[0]}-{years[-1]})")
        return None

    for geo, d in tidy.groupby("geography"):
        d_s = d[d["table_id"].isin(SUBJECT_TABLES)]
        d_b1 = d[d["table_id"] == "B27001"]
        d_b2 = d[d["table_id"] == "B27011"]
        available = sorted({int(v) for v in d["year"].unique()})
        if args.year is not None and args.year not in available:
            raise SystemExit(f"Year {args.year} not available for {geo}. Choose one of: {available}")
        slug = slugify(geo)
        figures = []

        if not d_s.empty:
            years = sorted(int(v) for v in d_s["year"].unique())
            year = pick_year(years, "S2703/S2704")
            if year is not None:
                figures += [
                    (f"1_coverage_by_type_{year}", chart_coverage_by_type(d_s, geo, year)),
                    (f"2_trends_{years[0]}_{years[-1]}", chart_trends(d_s, geo)),
                    (f"3_poverty_and_work_{year}",
                     chart_grouped(d_s, geo, year, [DIM_POVERTY, DIM_WORK],
                                   "Coverage by poverty status and employment", (10, 6.2), 18)),
                    (f"4_age_groups_{year}",
                     chart_grouped(d_s, geo, year, [DIM_AGE], "Coverage by age group", (12, 6.2), 12)),
                ]
        if not d_b1.empty:
            years = sorted(int(v) for v in d_b1["year"].unique())
            year = pick_year(years, "B27001")
            if year is not None:
                figures += [
                    (f"5_uninsured_by_age_{year}",
                     chart_grouped(d_b1, geo, year, [DIM_ALL, DIM_AGE_ALL], "Uninsured rate by age group",
                                   (11, 6.2), 12, groups=("Uninsured",),
                                   ylabel="Share of the group with no health insurance", footer=FOOTER_B)),
                    (f"6_uninsured_trend_{years[0]}_{years[-1]}", chart_uninsured_trend(d_b1, geo)),
                ]
        if not d_b2.empty:
            years = sorted(int(v) for v in d_b2["year"].unique())
            year = pick_year(years, "B27011")
            if year is not None:
                figures.append(
                    (f"7_coverage_by_employment_{year}",
                     chart_grouped(d_b2, geo, year, [DIM_LF], "Coverage by employment status, adults 19\u201364",
                                   (11, 6.2), 16, groups=("Private", "Public", "Uninsured"),
                                   ylabel="Share of the group", footer=FOOTER_B))
                )

        for name, fig in figures:
            if fig is None:
                print(f"  (skipped {name}: no data)")
                continue
            path = out / f"{slug}_{name}.png"
            fig.savefig(path, dpi=200)
            print(f"Saved {path}")

    if args.no_show:
        plt.close("all")
    else:
        plt.show()


if __name__ == "__main__":
    main()

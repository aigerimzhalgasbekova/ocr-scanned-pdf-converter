import argparse
import re
from pathlib import Path

import pandas as pd
import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageFilter, ImageOps

DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
HOLDER_SET = {"JT", "SP", "DC"}
TX_TYPES = ["Purchase", "Sale", "Exchange"]
AMOUNT_CODES = list("ABCDEFGHIJK")

# Fallback column ratios for forms similar to the attached PTR layout.
TX_FALLBACK = {
    "Purchase": 0.535,
    "Sale": 0.562,
    "Exchange": 0.589,
}
AMT_FALLBACK = {
    "A": 0.770,
    "B": 0.792,
    "C": 0.815,
    "D": 0.838,
    "E": 0.862,
    "F": 0.886,
    "G": 0.909,
    "H": 0.932,
    "I": 0.953,
    "J": 0.973,
    "K": 0.990,
}


def preprocess(img: Image.Image) -> Image.Image:
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.MedianFilter(size=3))
    img = img.point(lambda p: 255 if p > 180 else 0)
    return img


def rotate(img: Image.Image, angle: int) -> Image.Image:
    if angle == 0:
        return img
    return img.rotate(angle, expand=True, fillcolor=255)


def tsv_df(img: Image.Image, psm=6) -> pd.DataFrame:
    data = pytesseract.image_to_data(
        img,
        output_type=pytesseract.Output.DATAFRAME,
        config=f"--oem 3 --psm {psm}",
    )
    data = data.dropna(subset=["text"]).copy()
    data["text"] = data["text"].astype(str).str.strip()
    data = data[(data["text"] != "") & (data["conf"].fillna(-1) > -1)].copy()
    if data.empty:
        return data
    data["right"] = data["left"] + data["width"]
    data["bottom"] = data["top"] + data["height"]
    data["cx"] = data["left"] + data["width"] / 2
    data["cy"] = data["top"] + data["height"] / 2
    data["u"] = data["text"].str.upper()
    return data


def orientation_score(df: pd.DataFrame) -> float:
    if df.empty:
        return -1e9
    text = " ".join(df["u"].tolist())
    conf = df["conf"].clip(lower=0).mean() if "conf" in df else 0
    keywords = [
        "PURCHASE",
        "SALE",
        "EXCHANGE",
        "DATE",
        "TRANSACTION",
        "AMOUNT",
        "FULL",
        "ASSET",
        "NAME",
    ]
    score = sum(text.count(k) for k in keywords) * 20
    score += len(DATE_RE.findall(text)) * 6
    score += conf
    return score


def best_oriented_image(img: Image.Image) -> tuple[Image.Image, pd.DataFrame, int]:
    candidates = []
    for angle in [0, 90, 180, 270]:
        rimg = rotate(img, angle)
        rdf = tsv_df(rimg, psm=6)
        candidates.append((orientation_score(rdf), rimg, rdf, angle))
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, best_img, best_df, best_angle = candidates[0]
    return best_img, best_df, best_angle


def find_header_band(df: pd.DataFrame, height: int) -> int:
    if df.empty:
        return int(height * 0.35)

    header_words = df[
        df["u"].isin(
            [
                "PURCHASE",
                "SALE",
                "EXCHANGE",
                "DATE",
                "AMOUNT",
                "TRANSACTION",
                "ASSET",
                "NAME",
            ]
        )
    ]
    if not header_words.empty:
        return int(
            min(max(header_words["bottom"].max() + 25, height * 0.28), height * 0.55)
        )

    return int(height * 0.38)


def nearest(value, mapping):
    return min(mapping.items(), key=lambda kv: abs(kv[1] - value))[0]


def detect_tx_centers(df: pd.DataFrame, width: int) -> dict:
    tx = {}
    for name in TX_TYPES:
        hit = df[df["u"] == name.upper()]
        if not hit.empty:
            tx[name] = float(hit["cx"].median())

    if len(tx) == 3:
        return tx

    for name, ratio in TX_FALLBACK.items():
        tx.setdefault(name, width * ratio)
    return tx


def detect_amount_centers(df: pd.DataFrame, width: int, header_cutoff: int) -> dict:
    top = df[df["cy"] < header_cutoff].copy()
    letter_hits = top[top["u"].isin(AMOUNT_CODES)].copy()

    amount_cols = {}
    if not letter_hits.empty:
        grouped = (
            letter_hits.groupby("u", as_index=False)["cx"].median().sort_values("cx")
        )
        found = grouped["u"].tolist()
        if len(found) >= 6:
            for _, row in grouped.iterrows():
                amount_cols[row["u"]] = float(row["cx"])

    for code, ratio in AMT_FALLBACK.items():
        amount_cols.setdefault(code, width * ratio)

    return amount_cols


def collect_rows(df: pd.DataFrame, header_cutoff: int) -> list[list[dict]]:
    body = df[df["cy"] > header_cutoff].copy()
    if body.empty:
        return []

    words = body.sort_values(["cy", "cx"]).to_dict("records")
    row_groups = []
    tolerance = max(10, int(body["height"].median() * 0.9)) if not body.empty else 12

    for w in words:
        placed = False
        for group in row_groups:
            avg_y = sum(x["cy"] for x in group) / len(group)
            if abs(w["cy"] - avg_y) <= tolerance:
                group.append(w)
                placed = True
                break
        if not placed:
            row_groups.append([w])

    cleaned = []
    for group in row_groups:
        group = sorted(group, key=lambda r: r["cx"])
        text = " ".join(g["text"] for g in group)
        if len(text.strip()) < 2:
            continue
        cleaned.append(group)

    return cleaned


def classify_marks(group, tx_centers, amt_centers, date_left_boundary):
    tx_marks = []
    amt_marks = []

    for w in group:
        if w["u"] != "X":
            continue
        if w["cx"] < date_left_boundary:
            tx_marks.append(w)
        else:
            amt_marks.append(w)

    tx_type = ""
    amount_code = ""

    if tx_marks:
        chosen = min(
            tx_marks, key=lambda w: min(abs(w["cx"] - c) for c in tx_centers.values())
        )
        tx_type = nearest(chosen["cx"], tx_centers)

    if amt_marks:
        chosen = min(
            amt_marks, key=lambda w: min(abs(w["cx"] - c) for c in amt_centers.values())
        )
        amount_code = nearest(chosen["cx"], amt_centers)

    return tx_type, amount_code


def extract_dates(group):
    dates = []
    for w in group:
        for m in DATE_RE.findall(w["text"]):
            dates.append(m)
    return dates[:2]


def row_to_record(group, tx_centers, amt_centers):
    tx_left = min(tx_centers.values())
    date_left_boundary = min(amt_centers.values()) - 40

    holder = ""
    asset_words = []
    seen_dates = []
    tx_type, amount_code = classify_marks(
        group, tx_centers, amt_centers, date_left_boundary
    )

    for w in group:
        u = w["u"]
        if u in HOLDER_SET and not holder and w["cx"] < tx_left - 20:
            holder = u
            continue
        if DATE_RE.fullmatch(w["text"]):
            seen_dates.append(w["text"])
            continue
        if u == "X":
            continue
        if w["cx"] < tx_left - 10 and u not in HOLDER_SET:
            asset_words.append(w["text"])

    asset = " ".join(asset_words).strip()
    date_tx = seen_dates[0] if len(seen_dates) > 0 else ""
    date_notified = seen_dates[1] if len(seen_dates) > 1 else ""

    if not asset and not tx_type and not amount_code and not date_tx:
        return None

    return {
        "Holder": holder,
        "Asset": asset,
        "Transaction type": tx_type,
        "Date of transaction": date_tx,
        "Date notified": date_notified,
        "Amount code": amount_code,
    }


def is_probable_data_row(rec):
    if rec is None:
        return False
    text = (rec["Asset"] or "").upper()
    if not text and not rec["Date of transaction"]:
        return False
    junk_prefixes = [
        "FULL ASSET NAME",
        "PROVIDE FULL NAME",
        "DATE OF TRANSACTION",
        "DATE NOTIFIED",
        "AMOUNT OF TRANSACTION",
        "PURCHASE",
        "SALE",
        "EXCHANGE",
    ]
    return not any(text.startswith(j) for j in junk_prefixes)


def records_to_markdown(records, page_num):
    header = (
        "| Holder | Asset | Transaction type"
        " | Date of transaction | Date notified | Amount code |"
    )
    lines = [f"## Page {page_num}", "", header, "|---|---|---|---|---|---|"]
    for r in records:
        vals = [
            r.get("Holder", ""),
            r.get("Asset", "").replace("|", "\\|"),
            r.get("Transaction type", ""),
            r.get("Date of transaction", ""),
            r.get("Date notified", ""),
            r.get("Amount code", ""),
        ]
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")
    return "\n".join(lines)


def extract_page(img: Image.Image, page_num: int):
    pre = preprocess(img)
    oriented_img, df, angle = best_oriented_image(pre)
    width, height = oriented_img.size
    header_cutoff = find_header_band(df, height)
    tx_centers = detect_tx_centers(df, width)
    amt_centers = detect_amount_centers(df, width, header_cutoff)
    row_groups = collect_rows(df, header_cutoff)

    records = []
    for group in row_groups:
        rec = row_to_record(group, tx_centers, amt_centers)
        if is_probable_data_row(rec):
            records.append(rec)

    return {
        "page": page_num,
        "rotation": angle,
        "records": records,
        "markdown": records_to_markdown(records, page_num),
    }


def build_markdown(results, source_pdf):
    parts = [
        f"# OCR conversion for {Path(source_pdf).name}",
        "",
        "## Amount code legend",
        "",
        "| Code | Amount range |",
        "|---|---|",
        "| A | $1,000-$15,000 |",
        "| B | $15,001-$50,000 |",
        "| C | $50,001-$100,000 |",
        "| D | $100,001-$250,000 |",
        "| E | $250,001-$500,000 |",
        "| F | $500,001-$1,000,000 |",
        "| G | $1,000,001-$5,000,000 |",
        "| H | $5,000,001-$25,000,000 |",
        "| I | $25,000,001-$50,000,000 |",
        "| J | Over $50,000,000 |",
        "| K | Spouse/DC Amount over $1,000,000 |",
        "",
    ]
    for r in results:
        parts.append(r["markdown"])
    return "\n".join(parts).strip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="Input scanned PDF")
    ap.add_argument("-o", "--output", default=None, help="Output markdown path")
    ap.add_argument("--dpi", type=int, default=300, help="Render DPI")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    out_path = Path(args.output) if args.output else pdf_path.with_suffix(".md")

    pages = convert_from_path(str(pdf_path), dpi=args.dpi)
    results = []
    for i, page in enumerate(pages, start=1):
        result = extract_page(page, i)
        results.append(result)
        rotation = result["rotation"]
        rows = len(result["records"])
        print(f"Processed page {i}: rotation={rotation} rows={rows}")

    md = build_markdown(results, pdf_path)
    out_path.write_text(md, encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

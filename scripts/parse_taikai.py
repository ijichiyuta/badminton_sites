#!/usr/bin/env python3
"""愛知県高体連 県大会結果PDF（トーナメント様式）のパーサ試作。

対象様式: 第75〜80回県総体・新人戦の「結果一覧」PDF
  - 順位サマリーページ（順位 / 学校対抗 / 複 / 単 の列構成）
  - 種目別フルドローページ（左右32スロットの64ドロー、スコアはラウンド別X列）

出力: JSON {source, events: [{event, entrants, matches}], standings}
使い方: .venv/bin/python scripts/parse_taikai.py <pdf> <out.json>
"""
import json
import re
import sys
from collections import defaultdict

import pdfplumber

EVENT_HEADS = ["男子複", "男子単", "女子複", "女子単", "男子学校対抗", "女子学校対抗"]


def rows_from_words(words, ytol=4.0):
    """Y座標が近い単語を行にまとめる。"""
    rows = defaultdict(list)
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        placed = False
        for key in list(rows):
            if abs(key - w["top"]) <= ytol:
                rows[key].append(w)
                placed = True
                break
        if not placed:
            rows[w["top"]].append(w)
    return [(k, sorted(v, key=lambda w: w["x0"])) for k, v in sorted(rows.items())]


def parse_entry(text):
    """「樫尾 陽汰･中村 琉音(名経大市邨)」→ {names: [..], school}"""
    text = re.sub(r"\s+", " ", text).strip()
    m = re.match(r"^(.*)[（(]([^（()）]+)[）)]\s*$", text)
    if not m:
        return {"names": [text], "school": None, "raw": text}
    names_part, school = m.group(1).strip(), m.group(2).strip()
    names = [re.sub(r"\s+", " ", n).strip() for n in re.split(r"[･・]", names_part)]
    return {"names": names, "school": school, "raw": text}


def find_draw_page_events(page):
    """ページ内の種目名ヘッダを返す。"""
    words = page.extract_words()
    heads = [w for w in words if w["text"] in EVENT_HEADS]
    return heads, words


def numeric_tokens(words):
    out = []
    for w in words:
        t = w["text"]
        if re.fullmatch(r"\d{1,2}", t):
            out.append((w, [int(t)]))
        elif re.fullmatch(r"\d{1,2}-\d{1,2}", t):  # 「21-12」形式
            out.append((w, [int(x) for x in t.split("-")]))
    return out


def cluster_columns(xs, gap=25):
    """X座標のリストを列に束ねて中心値を返す。"""
    cols = []
    for x in sorted(xs):
        if cols and x - cols[-1][-1] <= gap:
            cols[-1].append(x)
        else:
            cols.append([x])
    return [(sum(c) / len(c), min(c), max(c)) for c in cols]


def parse_draw(page, event_name):
    """64ドローページ → entrants + matches"""
    W = page.width
    words = page.extract_words()
    # --- エントリ（学校名括弧付きの行、左右外縁）---
    def is_entry_word(w):
        return w["top"] > 160
    school_words = [w for w in words if "(" in w["text"] or "（" in w["text"]]
    left_zone = W * 0.30
    right_zone = W * 0.70
    left_rows = rows_from_words(
        [w for w in words if is_entry_word(w) and w["x0"] < left_zone
         and not re.fullmatch(r"[\d-]+", w["text"])], 6)
    right_rows = rows_from_words(
        [w for w in words if is_entry_word(w) and w["x1"] > right_zone
         and not re.fullmatch(r"[\d-]+", w["text"])], 6)

    def rows_to_entries(rows):
        entries = []
        for y, ws in rows:
            text = " ".join(w["text"] for w in ws)
            if "(" in text or "（" in text:
                entries.append((y, parse_entry(text)))
        return entries

    left_entries = rows_to_entries(left_rows)
    right_entries = rows_to_entries(right_rows)

    # --- スコアトークン（半面ごと・名前領域の内側のみ）---
    nums = numeric_tokens([w for w in words if w["top"] > 160])
    name_right_edge = max((w["x1"] for _, ws in left_rows for w in ws), default=0)
    name_left_edge = min((w["x0"] for _, ws in right_rows for w in ws), default=W)
    left_nums = [(w, v) for w, v in nums
                 if name_right_edge - 10 < w["x0"] < W * 0.52]
    right_nums = [(w, v) for w, v in nums
                  if W * 0.48 <= w["x0"] and w["x1"] < name_left_edge + 10]

    def side_scores(nums_side, y, tol=16.0):
        """スロットYに近い段積みスコア行（各選手の行に自分のゲーム得点）。"""
        got = [(w, v) for w, v in nums_side
               if abs(w["top"] - y) <= tol and "-" not in w["text"]]
        got.sort(key=lambda wv: wv[0]["x0"])
        return [x for _, v in got for x in v]

    def match_scores(nums_side, y_mid, tol=22.0):
        """試合中点に書かれる「21-16 21-18」形式（準決勝以降）。"""
        got = [(w, v) for w, v in nums_side
               if abs(w["top"] - y_mid) <= tol and "-" in w["text"]]
        got.sort(key=lambda wv: wv[0]["x0"])
        return [tuple(v) for _, v in got]

    def walk(entries, nums_side, half):
        """ラウンドごとに畳み込み。勝敗不明はスロットNoneで進め、親子リンクを記録。"""
        slots = [{"y": y, "entry": e, "src": None} for y, e in entries]
        matches = []
        rnd = 0
        while len(slots) > 1:
            rnd += 1
            nxt = []
            for i in range(0, len(slots) - 1, 2):
                a, b = slots[i], slots[i + 1]
                sa = side_scores(nums_side, a["y"])
                sb = side_scores(nums_side, b["y"])
                wins_a = sum(1 for x, y2 in zip(sa, sb) if x > y2)
                wins_b = sum(1 for x, y2 in zip(sa, sb) if y2 > x)
                winner = None
                if sa and sb and wins_a != wins_b:
                    winner = a if wins_a > wins_b else b
                games = None
                if not winner:
                    hy = match_scores(nums_side, (a["y"] + b["y"]) / 2)
                    if hy:
                        games = hy  # 勝者視点表記だが左右どちらかは別途解決
                mid = f"{half}{rnd}-{len(matches)}"
                matches.append({
                    "id": mid, "round": rnd, "half": half,
                    "a": a["entry"]["raw"] if a["entry"] else None,
                    "b": b["entry"]["raw"] if b["entry"] else None,
                    "child_a": a["src"], "child_b": b["src"],
                    "score_a": sa, "score_b": sb,
                    "games": games,
                    "winner": winner["entry"]["raw"] if winner and winner["entry"] else None,
                })
                nxt.append({
                    "y": (a["y"] + b["y"]) / 2,
                    "entry": winner["entry"] if winner else None,
                    "src": mid,
                })
            if len(slots) % 2 == 1:  # BYE枠は素通し
                nxt.append(slots[-1])
            slots = nxt
        return matches, slots[0] if slots else None

    # --- ブラケット罫線（太線=矩形の4辺として出るので中心線に統合）---
    def dedupe(edges, orient):
        segs = []
        for e in sorted(edges, key=lambda e: (e["top"], e["x0"])):
            if orient == "h":
                pos, lo, hi = e["top"], e["x0"], e["x1"]
            else:
                pos, lo, hi = e["x0"], e["top"], e["bottom"]
            for s in segs:
                if abs(s["pos"] - pos) <= 3 and lo <= s["hi"] + 3 and hi >= s["lo"] - 3:
                    s["pos"] = (s["pos"] + pos) / 2
                    s["lo"], s["hi"] = min(s["lo"], lo), max(s["hi"], hi)
                    break
            else:
                segs.append({"pos": pos, "lo": lo, "hi": hi})
        return segs

    Hs = dedupe([e for e in page.edges if e["orientation"] == "h" and e["top"] > 160], "h")
    Vs = dedupe([e for e in page.edges if e["orientation"] == "v" and e["top"] > 160], "v")
    Vs = [v for v in Vs if v["hi"] - v["lo"] > 8]  # 太線の端点キャップを除外

    def walk_geo(entries, nums_side, half):
        """垂直コネクタで対戦ペアを決める（BYE・不均等ドロー対応）。"""
        if not entries:
            return [], None
        # エントリ → 自分のライン（名前行の直下にある水平線）のY
        def line_y(name_top):
            cands = [h for h in Hs if name_top - 2 <= h["pos"] <= name_top + 22]
            if not cands:
                return name_top + 12
            return min(cands, key=lambda h: h["pos"] - name_top)["pos"]

        active = []  # {y, entry, src, round}
        for y, e in entries:
            active.append({"y": line_y(y), "entry": e, "src": None, "rnd": 0})
        if half == "L":
            vs = sorted([v for v in Vs if v["pos"] < W * 0.55], key=lambda v: v["pos"])
        else:
            vs = sorted([v for v in Vs if v["pos"] > W * 0.45], key=lambda v: -v["pos"])
        matches = []
        for v in vs:
            ia = ib = None
            for i, s in enumerate(active):
                if abs(s["y"] - v["lo"]) <= 5:
                    ia = i
                elif abs(s["y"] - v["hi"]) <= 5:
                    ib = i
            if ia is None or ib is None:
                continue
            a, b = active[ia], active[ib]
            sa = side_scores(nums_side, a["y"])
            sb = side_scores(nums_side, b["y"])
            wins_a = sum(1 for x, y2 in zip(sa, sb) if x > y2)
            wins_b = sum(1 for x, y2 in zip(sa, sb) if y2 > x)
            winner = None
            if sa and sb and wins_a != wins_b:
                winner = a if wins_a > wins_b else b
            games = None
            if not winner:
                hy = match_scores(nums_side, (a["y"] + b["y"]) / 2)
                if hy:
                    games = hy
            rnd = max(a["rnd"], b["rnd"]) + 1
            mid = f"{half}{rnd}-{len(matches)}"
            matches.append({
                "id": mid, "round": rnd, "half": half,
                "a": a["entry"]["raw"] if a["entry"] else None,
                "b": b["entry"]["raw"] if b["entry"] else None,
                "child_a": a["src"], "child_b": b["src"],
                "score_a": sa, "score_b": sb, "games": games,
                "winner": winner["entry"]["raw"] if winner and winner["entry"] else None,
            })
            # 勝者ラインの次セグメント: vのXから伸びる水平線
            nh = [h for h in Hs if v["lo"] - 3 <= h["pos"] <= v["hi"] + 3
                  and (abs(h["lo"] - v["pos"]) <= 4 if half == "L" else abs(h["hi"] - v["pos"]) <= 4)]
            ny = nh[0]["pos"] if nh else (a["y"] + b["y"]) / 2
            merged = {"y": ny, "entry": winner["entry"] if winner else None,
                      "src": mid, "rnd": rnd}
            for i in sorted([ia, ib], reverse=True):
                active.pop(i)
            active.append(merged)
        # 残りが1つなら半面勝者
        top = active[0] if len(active) == 1 else None
        return matches, top

    lmatches, lfinal = walk_geo(left_entries, left_nums, "L")
    rmatches, rfinal = walk_geo(right_entries, right_nums, "R")
    # 幾何で試合数が不足する場合（罫線が取れないPDF）は従来の連続ペア方式
    exp = len(left_entries) + len(right_entries) - 2
    if len(lmatches) + len(rmatches) < exp * 0.8:
        lmatches, lfinal = walk(left_entries, left_nums, "L")
        rmatches, rfinal = walk(right_entries, right_nums, "R")

    # --- 決勝（勝者・進出者は順位サマリーとの突合で後段確定）---
    final = {
        "round": "F",
        "a": lfinal["entry"]["raw"] if lfinal and lfinal["entry"] else None,
        "b": rfinal["entry"]["raw"] if rfinal and rfinal["entry"] else None,
        "winner": None,
    }

    entrants = [e for _, e in left_entries] + [e for _, e in right_entries]
    return {
        "event": event_name,
        "entrants": entrants,
        "n_left": len(left_entries), "n_right": len(right_entries),
        "matches": lmatches + rmatches,
        "final": final,
    }


def parse_standings(page):
    """順位サマリーページ → {event: [(rank, text)]}"""
    words = [w for w in page.extract_words() if w["top"] > 150]
    heads = [w for w in words if w["text"] in EVENT_HEADS or w["text"] == "順位"]
    if not any(w["text"] == "順位" for w in heads):
        return None
    # 順位列
    rank_x = next(w["x0"] for w in heads if w["text"] == "順位")
    ranks = [(w["top"], int(w["text"])) for w in words
             if abs(w["x0"] - rank_x) < 20 and re.fullmatch(r"\d{1,2}", w["text"])]
    ranks.sort()
    # 種目列（順位以外のヘッダ間で分割）
    ev_heads = sorted([w for w in heads if w["text"] != "順位"], key=lambda w: w["x0"])
    out = {}
    for i, h in enumerate(ev_heads):
        # 列の内容はヘッダ位置より左に食い込むことがあるので、隣接ヘッダとの中点で分割
        x_lo = (ev_heads[i - 1]["x0"] + h["x0"]) / 2 if i > 0 else rank_x + 15
        x_hi = (h["x0"] + ev_heads[i + 1]["x0"]) / 2 if i + 1 < len(ev_heads) else page.width
        col_words = [w for w in words if x_lo <= w["x0"] < x_hi and w["top"] > h["top"] + 5]
        rows = []
        for y, rank in ranks:
            near = [w for w in col_words if -10 <= w["top"] - y <= 20]
            text = " ".join(w["text"] for w in sorted(near, key=lambda w: (w["top"], w["x0"])))
            if text:
                rows.append({"rank": rank, "text": text})
        out[h["text"]] = rows
    return out


def norm(s):
    return re.sub(r"[\s　･・]", "", s or "")


def names_only(s):
    """「樫尾 陽汰･中村 琉音(名経大市邨)」→「樫尾陽汰中村琉音」"""
    return norm(re.sub(r"[（(][^（()）]*[）)]", "", s or ""))


def name_list(s):
    """エントリ文字列 → 正規化した個人名のリスト。"""
    body = re.sub(r"[（(][^（()）]*[）)]", "", s or "")
    return [norm(n) for n in re.split(r"[･・]", body) if norm(n)]


def entry_in(entry_text, target_norm):
    """エントリの全個人名が対象文字列に含まれるか（校名の挟み込みに耐性）。"""
    names = name_list(entry_text)
    return bool(names) and all(n in target_norm for n in names)


def resolve(result):
    """順位サマリーとラウンド間の整合で未決着の試合を確定させる。"""
    ranked = {}  # event -> {rank: norm_text}
    for st in result["standings"]:
        for ev_name, rows in st["tables"].items():
            d = ranked.setdefault(ev_name, {})
            for r in rows:
                d.setdefault(r["rank"], []).append(norm(r["text"]))

    for ev in result["events"]:
        g = defaultdict(list)  # (half, round) -> matches
        for m in ev["matches"]:
            g[(m["half"], m["round"])].append(m)
        if not g:
            continue
        maxr = max(r for _, r in g)
        finalists = ranked.get(ev["event"], {}).get(1, []) + \
            ranked.get(ev["event"], {}).get(2, [])

        # 半面決勝の勝者 = 決勝進出者（rank1/2のうちその半面のエントラントと一致する方）
        for half in ("L", "R"):
            hf = g.get((half, maxr))
            if not hf or hf[0]["winner"]:
                continue
            half_entrants = [t for m in g[(half, 1)] for t in (m["a"], m["b"]) if t]
            for ftext in finalists:
                hit = [e for e in half_entrants if entry_in(e, ftext)]
                if len(hit) == 1:
                    hf[0]["winner"] = hit[0]
                    break

        # 収束するまで親子リンクで上下に伝播
        by_id = {m["id"]: m for m in ev["matches"]}
        changed = True
        while changed:
            changed = False
            for m in ev["matches"]:
                for ckey, skey in (("child_a", "a"), ("child_b", "b")):
                    c = by_id.get(m.get(ckey) or "")
                    if not c:
                        continue
                    # 下から: 子試合の勝者で不明サイドを埋める
                    if m[skey] is None and c["winner"]:
                        m[skey] = c["winner"]
                        changed = True
                    # 上から: 親試合に載っているサイド名 = 子試合の勝者
                    if m[skey] and not c["winner"]:
                        c["winner"] = m[skey]
                        changed = True

        # 決勝
        f = ev.get("final")
        if f is not None:
            for half, key in (("L", "a"), ("R", "b")):
                if f[key] is None and (half, maxr) in g and g[(half, maxr)][0]["winner"]:
                    f[key] = g[(half, maxr)][0]["winner"]
            champs = ranked.get(ev["event"], {}).get(1, [])
            for champ in champs:
                for key in ("a", "b"):
                    if f[key] and entry_in(f[key], champ):
                        f["winner"] = f[key]
                        break


def main(pdf_path, out_path):
    result = {"source": pdf_path, "events": [], "standings": []}
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            heads, words = find_draw_page_events(page)
            texts = [h["text"] for h in heads]
            has_rank = any(w["text"] == "順位" for w in page.extract_words())
            if has_rank:
                st = parse_standings(page)
                if st:
                    result["standings"].append({"page": i, "tables": st})
            elif len(texts) == 1 and texts[0] in ("男子複", "男子単", "女子複", "女子単"):
                ev = parse_draw(page, texts[0])
                ev["page"] = i
                result["events"].append(ev)
            else:
                result.setdefault("skipped_pages", []).append(
                    {"page": i, "heads": texts})
    resolve(result)
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    # サマリー表示
    for ev in result["events"]:
        done = sum(1 for m in ev["matches"] if m["winner"])
        print(f"{ev['event']}: entrants={ev['n_left']}+{ev['n_right']} "
              f"matches={len(ev['matches'])} decided={done}")
    print(f"standings pages: {len(result['standings'])}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

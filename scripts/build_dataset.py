#!/usr/bin/env python3
"""パース済みJSON群 → ダッシュボード用 data.json を生成する。

品質方針: 対戦の両サイドと勝者が確定している試合のみ matches に含める。
（誤ペアリングの可能性があるデータは出さない）
"""
import json
import glob
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARSED = ROOT / "data" / "aichi" / "parsed"
OUT = ROOT / "web" / "data.json"

TOURNAMENTS = {
    "2021_soutai75": {"label": "第75回県高校総体", "year": 2021, "series": "総体"},
    "2022_soutai76": {"label": "第76回県高校総体", "year": 2022, "series": "総体"},
    "2023_soutai77": {"label": "第77回県高校総体", "year": 2023, "series": "総体"},
    "2024_soutai78": {"label": "第78回県高校総体", "year": 2024, "series": "総体"},
    "2025_soutai79": {"label": "第79回県高校総体", "year": 2025, "series": "総体"},
    "2026_soutai80": {"label": "第80回県高校総体", "year": 2026, "series": "総体"},
    "2022_shinjin_r4": {"label": "令和4年度新人戦", "year": 2022, "series": "新人戦"},
    "2023_shinjin_r5": {"label": "令和5年度新人戦", "year": 2023, "series": "新人戦"},
    "2024_shinjin_r6": {"label": "令和6年度新人戦", "year": 2024, "series": "新人戦"},
    "2025_shinjin_r7": {"label": "令和7年度新人戦", "year": 2025, "series": "新人戦"},
}


def norm(s):
    return re.sub(r"[\s　･・]", "", s or "")


def split_entry(raw):
    """「樫尾 陽汰･中村 琉音(名経大市邨)」→ (names[], school)"""
    m = re.match(r"^(.*?)[（(]([^（()）]+)[）)]\s*$", raw or "")
    if not m:
        return [raw], None
    names = [n.strip() for n in re.split(r"[･・]", m.group(1)) if n.strip()]
    return names, m.group(2).strip()


def main():
    tournaments = []
    matches = []
    standings = []
    players = {}  # key -> {name, school, tournaments:set}

    def touch_player(name, school, tid, event):
        key = f"{norm(name)}|{school or ''}"
        p = players.setdefault(key, {
            "key": key, "name": re.sub(r"\s+", " ", name.strip()),
            "school": school, "apps": []})
        app = {"t": tid, "event": event}
        if app not in p["apps"]:
            p["apps"].append(app)
        return key

    for tid, meta in TOURNAMENTS.items():
        f = PARSED / f"{tid}.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text())
        n_matches = 0
        n_entrants = 0
        for ev in d.get("events", []):
            event = ev["event"]
            for e in ev.get("entrants", []):
                n_entrants += 1
                for nm in e.get("names", []):
                    touch_player(nm, e.get("school"), tid, event)
            evms = list(ev.get("matches", []))
            fin = ev.get("final")
            if fin and fin.get("a") and fin.get("b"):
                evms.append({"round": "F", "half": "", "a": fin["a"],
                             "b": fin["b"], "score_a": [], "score_b": [],
                             "winner": fin.get("winner")})
            maxr = max((m["round"] for m in evms if isinstance(m["round"], int)),
                       default=0)
            for m in evms:
                if not (m.get("a") and m.get("b") and m.get("winner")):
                    continue
                r = m["round"]
                rlabel = ("決勝" if r == "F" else
                          "準決勝" if r == maxr else
                          "準々決勝" if r == maxr - 1 else f"{r}回戦")
                an, aschool = split_entry(m["a"])
                bn, bschool = split_entry(m["b"])
                rec = {
                    "t": tid, "event": event, "round": rlabel,
                    "ord": (99 if r == "F" else r),
                    "a": m["a"], "b": m["b"], "winner": m["winner"],
                    "sa": m.get("score_a") or [], "sb": m.get("score_b") or [],
                    "pa": [f"{norm(n)}|{aschool or ''}" for n in an],
                    "pb": [f"{norm(n)}|{bschool or ''}" for n in bn],
                }
                matches.append(rec)
                n_matches += 1
        for st in d.get("standings", []):
            for ev_name, rows in st.get("tables", {}).items():
                standings.append({"t": tid, "event": ev_name, "rows": rows})
        tournaments.append({
            "id": tid, **meta,
            "entrants": n_entrants, "matches": n_matches,
        })

    out = {
        "tournaments": tournaments,
        "matches": matches,
        "standings": standings,
        "players": sorted(players.values(),
                          key=lambda p: (p["school"] or "", p["name"])),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False))
    print(f"tournaments={len(tournaments)} matches={len(matches)} "
          f"players={len(players)} standings={len(standings)}")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main()

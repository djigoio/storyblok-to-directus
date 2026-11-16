#!/usr/bin/env python3
"""
Seed dummy content into the Directus schema created by storyblok_to_directus.py.

  python3 scripts/seed_dummy_data.py --dry-run   # writes scripts/seed-data.json, touches nothing
  python3 scripts/seed_dummy_data.py             # uploads placeholder images + creates content

What it creates
  * ~10 generated SVG placeholder images (uploaded into a "Storyblok demo seed" file folder)
  * stories for every content-type collection (prizes, seasons, season passes, pages, footers, …)
  * nested block trees inside each story (Directus nested many-to-any create), choosing
    blocks so that every block type is used at least once
  * story references (m2o) wired to the seeded entities

Deterministic (fixed random seed) with client-side UUIDs, so re-running skips stories that
already exist. Auth: same env/.env as storyblok_to_directus.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from storyblok_to_directus import ROOT, SCHEMA_DIR, Directus, Planner, connect, snake  # noqa: E402

MAX_DEPTH = 3
NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.timezone.utc)

STORY_COUNTS = {
    "prize": 6, "season": 3, "season_pass": 2, "technical_data": 3, "intermediate_prize_story": 4,
    "media": 4, "content_page": 3, "prize_page": 3, "season_page": 3, "season_pass_page": 2,
    "redirect_page": 2, "announcement_banner": 2,
}
DEFAULT_STORY_COUNT = 1
# Blocks that only exist for debugging/experiments: never picked at random from broad lists.
AVOID_RANDOM = {"block_debug_theme_preview", "block_colors_test", "block_experiment",
                "block_experiment_variant", "block_reference"}

TITLES = [
    "Gewinne dein neues Zuhause", "Ein Los, viele Chancen", "Jetzt Los sichern",
    "Die große Sommer-Verlosung", "So funktioniert's", "Unsere glücklichen Gewinner",
    "Häufige Fragen", "Gemeinsam Gutes tun", "Dein Season Pass", "Sonderverlosung im Dezember",
    "Wohnen am See", "Alle Gewinne im Überblick", "Bekannt aus", "Deine Vorteile",
    "Nur noch wenige Tage", "Das Stadthaus am Park", "Exklusive Zwischengewinne",
]
SENTENCES = [
    "Mit jedem Los unterstützt du soziale Projekte in ganz Deutschland.",
    "Die Ziehung findet live statt und wird notariell überwacht.",
    "Sichere dir jetzt dein Los und sei bei allen Ziehungen der Saison dabei.",
    "Unsere Gewinner erzählen, wie sich ihr Leben verändert hat.",
    "Ein modernes Haus mit Garten, Terrasse und viel Platz für die ganze Familie.",
    "Zusätzlich zum Hauptgewinn warten jeden Monat attraktive Zwischengewinne.",
    "Kündigung jederzeit möglich – ganz ohne versteckte Kosten.",
    "Ein Teil jedes Loses geht direkt an unsere Partnerorganisationen.",
]
BUTTONS = ["Jetzt mitspielen", "Mehr erfahren", "Los kaufen", "Zum Season Pass", "Alle Gewinne ansehen"]
# Fictional placeholder names only (GDPR: never use real people in demo data)
NAMES = ["Max Mustermann", "Erika Mustermann", "Otto Normalverbraucher", "Lieschen Müller",
         "Hans Beispiel", "Petra Platzhalter"]
ICONS = ["star", "gift", "home", "ticket", "heart", "check", "calendar", "trophy"]
EMOJIS = ["🏠", "🎉", "🚗", "✈️", "💶", "🎁"]
VIDEO_IDS = ["demo-video-0001", "demo-video-0002", "demo-video-0003"]
URLS = ["https://example.com/teilnahmebedingungen", "/season-pass", "/gewinne", "/faq",
        "https://example.com/datenschutz", "/so-funktionierts"]
COLORS = ["#FF6F08", "#1E3A8A", "#0EA5E9", "#16A34A", "#9333EA", "#F43F5E"]
IMAGE_LABELS = ["Hauptgewinn", "Stadtvilla", "Seehaus", "Gewinnerin", "Gewinner", "Auto",
                "Reise", "Charity", "Logo", "Season Pass"]


def kebab(s: str) -> str:
    s = (s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


class Seeder:
    def __init__(self, planner: Planner, seed: int = 42):
        self.p = planner
        self.rng = random.Random(seed)
        self.fields = {c["collection"]: c["fields"] for c in planner.collections}
        self.m2o = {(r["collection"], r["field"]): r["related_collection"] for r in planner.relations
                    if r.get("related_collection") and r["related_collection"] not in ("directus_files", "directus_users")
                    and not (r.get("meta") or {}).get("one_field")}
        self.story_colls = [planner.coll[c["name"]] for c in planner.components if planner.is_story(c)]
        self.story_ids: dict[str, list[str]] = {}
        self.used_blocks: set[str] = set()
        self.all_blocks = sorted(planner.coll[c["name"]] for c in planner.components if c.get("is_nestable"))
        self.terminal = {b for b in self.all_blocks if not self._has_required_children(b)}
        self.n_images = len(IMAGE_LABELS)

    # ---------------------------------------------------------------- helpers
    def uid(self) -> str:
        return str(uuid.UUID(int=self.rng.getrandbits(128), version=4))

    def _has_required_children(self, coll: str) -> bool:
        return any(info["required"] or (info["min"] or 0) > 0
                   for (c, _), info in self.p.m2a_info.items() if c == coll and not info["link_existing"])

    def deps(self, coll: str, seen=None) -> set[str]:
        """Story collections that `coll` (or blocks nested in it) references via m2o / link-existing m2a."""
        seen = seen if seen is not None else set()
        if coll in seen:
            return set()
        seen.add(coll)
        out = {t for (c, _), t in self.m2o.items() if c == coll}
        for (c, _), info in self.p.m2a_info.items():
            if c != coll:
                continue
            if info["link_existing"]:
                out |= set(info["allowed"])
            else:
                for b in info["allowed"]:
                    out |= self.deps(b, seen)
        out.discard(coll)
        return out

    def story_order(self) -> list[str]:
        deps = {c: self.deps(c) & set(self.story_colls) for c in self.story_colls}
        order, done = [], set()
        pending = sorted(self.story_colls)
        while pending:
            ready = [c for c in pending if deps[c] <= done] or [pending[0]]  # break cycles
            for c in ready:
                order.append(c)
                done.add(c)
                pending.remove(c)
        return order

    # ---------------------------------------------------------------- values
    def text_value(self, field: str, meta: dict, schema: dict):
        n = field.lower()
        r = self.rng
        if "videoid" in n or "video_id" in n:
            return r.choice(VIDEO_IDS)
        if n in ("emoji",):
            return r.choice(EMOJIS)
        if "icon" in n:
            return r.choice(ICONS)
        if "color" in n or "bg_override" in n:
            return r.choice(COLORS)
        if n in ("slug",):
            return kebab(r.choice(TITLES))
        if n in ("sb_id", "id", "draw_id"):
            return f"{'DRAW' if 'draw' in n else 'ID'}_{r.randint(100, 999)}"
        if n == "elementid":
            return f"section-{r.randint(1, 9)}"
        if n in ("value", "count"):
            return str(r.choice([12, 98.5, 250, 1500, 7]))
        if "additionalnumbers" in n:
            return ",".join(str(r.randint(1, 49)) for _ in range(3))
        if n.endswith("sum") or "prefix" in n:
            return r.choice(["750.000 €", "1,2 Mio. €", "2.400 €/Monat", "Im Wert von"])
        if n.endswith("path") or n.endswith("url"):
            return r.choice(URLS)
        if "name" in n:
            return r.choice(NAMES)
        if any(k in n for k in ("button", "cta", "linktext", "label")):
            return r.choice(BUTTONS)
        if any(k in n for k in ("description", "subline", "subtitle", "text", "note", "ics_")):
            return r.choice(SENTENCES)
        return r.choice(TITLES)

    def regex_ok(self, meta: dict, value) -> bool:
        v = meta.get("validation")
        if not v or value is None:
            return True
        pattern = list(v["_and"][0].values())[0]["_regex"]
        return re.search(pattern, str(value)) is not None

    def regex_value(self, field: str, meta: dict, schema: dict):
        candidates = [self.text_value(field, meta, schema), "PRIZE_001", "98.5", "123", "#FF6F08",
                      "beispiel-slug", "7,13,42", "ABC", "section-1"]
        for c in candidates:
            if self.regex_ok(meta, c):
                return c
        return None

    def field_value(self, coll: str, fld: dict, depth: int):
        name, ftype = fld["field"], fld["type"]
        meta, schema = fld.get("meta") or {}, fld.get("schema") or {}
        special = meta.get("special") or []
        required = bool(meta.get("required"))
        r = self.rng
        # optional fields are filled most of the time
        if not required and r.random() < 0.25 and "m2a" not in special:
            return None

        if "m2a" in special:
            return self.m2a_value(coll, name, depth)
        if "file" in special:
            return f"__FILE_{r.randrange(self.n_images)}__"
        if "m2o" in special:
            target = self.m2o.get((coll, name))
            ids = self.story_ids.get(target) or []
            return r.choice(ids) if ids else None
        if ftype == "boolean":
            if any(k in name.lower() for k in ("hide", "hidden", "disable", "debug", "target_blank")):
                return False
            return r.random() < 0.6
        if ftype in ("integer", "float"):
            o = meta.get("options") or {}
            lo, hi = o.get("min", 1), o.get("max", 100)
            lo, hi = (lo if lo is not None else 1), (hi if hi is not None else max(lo, 0) + 100)
            return r.randint(int(lo), int(hi)) if ftype == "integer" else round(r.uniform(lo, hi), 1)
        if ftype in ("timestamp", "date"):
            n = name.lower()
            future = any(k in n for k in ("to", "end", "until", "draw"))
            d = NOW + dt.timedelta(days=r.randint(10, 90) if future else -r.randint(1, 30), hours=r.randint(0, 12))
            return d.date().isoformat() if ftype == "date" else d.isoformat().replace("+00:00", "Z")
        if ftype == "json":
            choices = (meta.get("options") or {}).get("choices") or []
            return [c["value"] for c in r.sample(choices, min(2, len(choices)))] if choices else []
        if ftype == "uuid":
            return None
        # strings / text
        choices = (meta.get("options") or {}).get("choices")
        if meta.get("interface") == "select-dropdown" and choices:
            return r.choice(choices)["value"]
        if meta.get("interface") == "select-color":
            return r.choice(COLORS)
        if meta.get("interface") == "input-rich-text-html":
            return f"<h2>{r.choice(TITLES)}</h2><p>{r.choice(SENTENCES)} <strong>{r.choice(SENTENCES)}</strong></p>"
        if meta.get("interface") == "input-multiline":
            return " ".join(r.sample(SENTENCES, 2))
        if "multilink" in (meta.get("note") or ""):
            return r.choice(URLS)
        if meta.get("validation"):
            return self.regex_value(name, meta, schema)
        val = self.text_value(name, meta, schema)
        ml = schema.get("max_length")
        return val[:ml] if ml and isinstance(val, str) else val

    def pick_blocks(self, allowed: list[str], count: int, depth: int) -> list[str]:
        r = self.rng
        pool = [b for b in allowed if b not in AVOID_RANDOM] or list(allowed)
        if depth >= MAX_DEPTH - 1:
            pool = [b for b in pool if b in self.terminal] or pool
        picks = []
        for _ in range(count):
            unused = [b for b in pool if b not in self.used_blocks and b not in picks]
            choice = r.choice(unused) if unused else r.choice(pool)
            picks.append(choice)
            self.used_blocks.add(choice)
        return picks

    def m2a_value(self, coll: str, field: str, depth: int):
        info = self.p.m2a_info[(coll, field)]
        allowed = info["allowed"]
        if not allowed:
            return None
        mn = info["min"] or (1 if info["required"] else 0)
        mx = info["max"] or (5 if len(allowed) > 10 else 3)
        if info["link_existing"]:
            existing = [(c, i) for c in allowed for i in self.story_ids.get(c, [])]
            if not existing:
                return None
            c, i = self.rng.choice(existing)
            return [{"collection": c, "item": i, "sort": 1}]
        if depth >= MAX_DEPTH:
            count = mn
        elif len(allowed) > 10 and depth == 0:
            count = max(mn, min(mx, self.rng.randint(4, 6)))
        else:
            count = max(mn, min(mx, self.rng.randint(1, 3)))
        if count == 0:
            return None
        return [{"collection": b, "item": self.block_item(b, depth + 1), "sort": i + 1}
                for i, b in enumerate(self.pick_blocks(allowed, count, depth))]

    def block_item(self, coll: str, depth: int) -> dict:
        item = {}
        for fld in self.fields[coll]:
            meta = fld.get("meta") or {}
            special = meta.get("special") or []
            if fld["field"] == "id" or "group" in special or any(s.startswith(("date-", "user-")) for s in special):
                continue
            if fld["field"] in ("status", "name", "slug") and coll in self.story_colls:
                continue
            v = self.field_value(coll, fld, depth)
            if v is not None:
                item[fld["field"]] = v
        if coll in self.story_colls:  # root+nestable used inline
            item.setdefault("status", "published")
        return item

    # ---------------------------------------------------------------- build
    def build(self) -> dict:
        order = self.story_order()
        for c in order:
            self.story_ids[c] = [self.uid() for _ in range(STORY_COUNTS.get(c, DEFAULT_STORY_COUNT))]
        planned = {c: list(ids) for c, ids in self.story_ids.items()}
        self.story_ids = {}  # only expose ids of stories created before the current one (FK order)

        stories = []
        for c in order:
            label = next(x for x in self.p.collections if x["collection"] == c)["meta"]["translations"][0]["translation"]
            done = []
            for idx, sid in enumerate(planned[c]):
                item = self.block_item(c, 0)
                title = f"{label} {idx + 1}"
                if c == "prize":
                    title = f"{self.rng.choice(['Hauptgewinn', 'Stadtvilla', 'Seehaus', 'Penthouse'])} {idx + 1}"
                elif c == "season":
                    title = f"Saison {2026 + idx}"
                item.update({"id": sid, "name": title, "slug": f"{kebab(label)}/{kebab(title)}",
                             "status": "draft" if idx == 2 else "published"})
                stories.append({"collection": c, "item": item})
                done.append(sid)
            self.story_ids[c] = done

        unused = sorted(set(self.all_blocks) - self.used_blocks - AVOID_RANDOM)
        # Make sure every block type appears once: a showcase page holding all unused blocks.
        showcase = None
        page = "content_page"
        field = next((f for (c, f) in self.p.m2a_info if c == page), None)
        if unused and field:
            allowed = set(self.p.m2a_info[(page, field)]["allowed"])
            rows = [{"collection": b, "item": self.block_item(b, 1), "sort": i + 1}
                    for i, b in enumerate(b for b in unused if b in allowed)]
            nested_hosts = [b for b in unused if b not in allowed]
            showcase = self.block_item(page, MAX_DEPTH)  # depth MAX so its own lists stay minimal
            showcase.update({"id": self.uid(), "name": "Block Showcase", "slug": "showcase/alle-bloecke",
                             "status": "draft", field: rows})
            stories.append({"collection": page, "item": showcase})
            del nested_hosts

        # Blocks no bloks field allows (orphans in Storyblok too) get standalone items.
        seen = set()

        def walk(coll, item):
            seen.add(coll)
            for v in item.values():
                if isinstance(v, list):
                    for row in v:
                        if isinstance(row, dict) and isinstance(row.get("item"), dict):
                            walk(row["collection"], row["item"])
        for s in stories:
            walk(s["collection"], s["item"])
        for b in sorted(set(self.all_blocks) - seen):
            item = self.block_item(b, 1)
            item["id"] = self.uid()
            stories.append({"collection": b, "item": item})
        return {"images": IMAGE_LABELS, "stories": stories}


# ---------------------------------------------------------------- images / upload

def svg_placeholder(label: str, i: int) -> bytes:
    a, b = COLORS[i % len(COLORS)], COLORS[(i + 2) % len(COLORS)]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient></defs>
<rect width="1200" height="800" fill="url(#g)"/>
<text x="600" y="420" font-family="Helvetica, Arial, sans-serif" font-size="88" font-weight="700" fill="#fff" text-anchor="middle">{label}</text>
<text x="600" y="500" font-family="Helvetica, Arial, sans-serif" font-size="32" fill="#fff" fill-opacity=".8" text-anchor="middle">Demo-Platzhalter {i + 1}</text>
</svg>""".encode()


def upload_images(api: Directus, labels: list[str]) -> list[str]:
    folder_name = "Storyblok demo seed"
    folders = api.req("GET", f"/folders?filter[name][_eq]={folder_name.replace(' ', '%20')}")["data"]
    folder = folders[0]["id"] if folders else api.req("POST", "/folders", {"name": folder_name})["data"]["id"]
    existing = api.req("GET", f"/files?filter[folder][_eq]={folder}&limit=-1&fields=id,title&sort=title")["data"]
    by_title = {f["title"]: f["id"] for f in existing}
    ids = []
    for i, label in enumerate(labels):
        t = f"seed-{i:02d} {label}"
        if t in by_title:
            ids.append(by_title[t])
            continue
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (("folder", folder), ("title", t)):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{kebab(t)}.svg"\r\n'
                     f"Content-Type: image/svg+xml\r\n\r\n".encode() + svg_placeholder(label, i) + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        import urllib.request
        req = urllib.request.Request(api.url + "/files", data=b"".join(parts), method="POST")
        req.add_header("Authorization", f"Bearer {api.token}")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            ids.append(json.loads(resp.read())["data"]["id"])
        print(f"  + file {t}")
    return ids


def substitute_files(obj, ids: list[str]):
    if isinstance(obj, dict):
        return {k: substitute_files(v, ids) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_files(v, ids) for v in obj]
    if isinstance(obj, str):
        m = re.fullmatch(r"__FILE_(\d+)__", obj)
        if m:
            return ids[int(m.group(1)) % len(ids)]
    return obj


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=str(ROOT / "scripts" / "seed-data.json"))
    args = ap.parse_args()

    sd = SCHEMA_DIR
    planner = Planner(json.loads((sd / "components.json").read_text()),
                      json.loads((sd / "groups.json").read_text()),
                      json.loads((sd / "tags.json").read_text()))
    planner.build()
    data = Seeder(planner, args.seed).build()
    Path(args.out).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def count_blocks(o):
        if isinstance(o, dict):
            return (1 if "collection" in o and isinstance(o.get("item"), dict) else 0) + sum(count_blocks(v) for v in o.values())
        if isinstance(o, list):
            return sum(count_blocks(v) for v in o)
        return 0
    print(f"Seed: {len(data['stories'])} stories, {count_blocks(data['stories']) - len(data['stories'])} nested blocks, "
          f"{len(data['images'])} images -> {args.out}")
    if args.dry_run:
        return

    api = connect()
    print("\n== files")
    ids = upload_images(api, data["images"])
    print("\n== stories")
    errors = 0
    for s in data["stories"]:
        c, item = s["collection"], substitute_files(s["item"], ids)
        try:
            exists = api.req("GET", f"/items/{c}?filter[id][_eq]={item['id']}&fields=id")["data"]
            label = item.get("name") or item["id"][:8]
            if exists:
                print(f"  = {c} {label} (exists)")
                continue
            api.req("POST", f"/items/{c}", item)
            print(f"  + {c}: {label}")
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  ! {c} {item.get('name')}: {e}")
    print(f"\nDone with {errors} error(s).")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

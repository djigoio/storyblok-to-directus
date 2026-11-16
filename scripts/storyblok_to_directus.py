#!/usr/bin/env python3
"""
Storyblok -> Directus schema generator.

Reads the Storyblok export in ../storyblok-schema (components.json + groups.json)
and recreates it as closely as possible in a Directus instance via the REST API.

Mapping summary
---------------
  Storyblok component (content type)  -> collection  `<snake_name>`  (+ story fields: name, slug, status, dates)
  Storyblok component (nestable blok) -> collection  `block_<snake_name>`
  Component group                     -> collection folder (`folder_<group>`)
  text                                -> string (input; regex/max_length kept as validation/length)
  text + storyblok-colorpicker        -> string (select-color)
  textarea                            -> text (input-multiline)
  richtext                            -> text (input-rich-text-html, toolbar mapped)
  markdown                            -> text (input-rich-text-md)
  number                              -> integer / float (min/max/step kept)
  boolean                             -> boolean (toggle)
  datetime                            -> timestamp (or date when time is disabled)
  option (inline values)              -> string (select-dropdown with choices)
  option/options -> internal story    -> many-to-one to that content-type collection
  asset                               -> many-to-one to directus_files (file-image / file)
  multilink                           -> string URL (+ `<field>_target_blank` boolean when allowed)
  bloks                               -> many-to-any (junction `<collection>_<field>`) with the
                                         whitelist / group-whitelist resolved to allowed collections
  tab                                 -> group-detail field group
  custom (emoji-picker)               -> string

Usage
-----
  python3 scripts/storyblok_to_directus.py --dry-run          # writes the plan JSON, touches nothing
  python3 scripts/storyblok_to_directus.py                    # applies (idempotent: skips existing)
  python3 scripts/storyblok_to_directus.py --reset --yes      # deletes everything this script creates

Auth (read from env or a `.env` file in the project root):
  DIRECTUS_URL       default http://localhost:8055
  DIRECTUS_TOKEN     static admin token  (preferred)
  or DIRECTUS_EMAIL + DIRECTUS_PASSWORD  (logs in to get a short-lived token)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "storyblok-schema"
LANG = "en-US"

STORY_FIELDS = {"id", "name", "slug", "status", "date_created", "date_updated", "user_created", "user_updated"}
BLOCK_FIELDS = {"id"}
RENAME_PREFIX = "sb_"

GROUP_ORDER = ["Pages", "Entities", "Global", "Blocks", "Block Children",
               "Season Page Blocks", "House Blocks", "App Blocks"]
GROUP_ICONS = {
    "Pages": "article", "Entities": "category", "Global": "public", "Blocks": "widgets",
    "Block Children": "view_module", "Season Page Blocks": "event", "House Blocks": "home",
    "App Blocks": "smartphone",
}

# Storyblok richtext toolbar -> Directus input-rich-text-html toolbar
TOOLBAR_MAP = {
    "bold": ["bold"], "italic": ["italic"], "underline": ["underline"], "strike": ["strikethrough"],
    "subscript": ["subscript"], "superscript": ["superscript"],
    "h1": ["h1"], "h2": ["h2"], "h3": ["h3"], "h4": ["h4"], "h5": ["h5"], "h6": ["h6"],
    "list": ["bullist"], "olist": ["numlist"], "quote": ["blockquote"], "link": ["customLink"],
    "image": ["customImage"], "code": ["codeblock"], "inlinecode": ["code"], "hrule": ["hr"],
    "unset": ["removeformat"], "color": ["forecolor"], "highlight": ["backcolor"], "add-table": ["table"],
    "undo": ["undo"], "redo": ["redo"],
}
DEFAULT_TOOLBAR = ["bold", "italic", "underline", "h2", "h3", "bullist", "numlist",
                   "blockquote", "customLink", "removeformat", "code", "fullscreen"]


# --------------------------------------------------------------------------- helpers

def snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_").lower()


def title(name: str) -> str:
    return " ".join(w.capitalize() for w in snake(name).split("_"))


def tr(text: str | None, singular: str | None = None, plural: str | None = None):
    if not text:
        return None
    t = {"language": LANG, "translation": text}
    if singular is not None:
        t["singular"] = singular
        t["plural"] = plural or text
    return [t]


def clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def to_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v) if v is not None else None


def to_num(v, as_float: bool):
    if v in (None, ""):
        return None
    try:
        return float(v) if as_float else int(float(v))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- plan

class Planner:
    def __init__(self, components: list[dict], groups: list[dict], tags: list[dict]):
        self.components = components
        self.groups = {g["uuid"]: g for g in groups}
        self.tags = {t["id"]: t["name"] for t in tags}
        self.by_name = {c["name"]: c for c in components}
        self.by_lower = {c["name"].lower(): c for c in components}
        self.coll = {c["name"]: self.collection_name(c) for c in components}
        self.folders: list[dict] = []
        self.collections: list[dict] = []
        self.junctions: list[dict] = []
        self.relations: list[dict] = []
        self.report: list[str] = []
        self.m2a_info: dict[tuple[str, str], dict] = {}
        self._check_unique()

    # -- naming -------------------------------------------------------------
    @staticmethod
    def collection_name(c: dict) -> str:
        base = snake(c["name"])
        return f"block_{base}" if c.get("is_nestable") else base

    @staticmethod
    def is_story(c: dict) -> bool:
        return bool(c.get("is_root"))

    def folder_key(self, group_uuid: str | None) -> str | None:
        g = self.groups.get(group_uuid or "")
        return f"folder_{snake(g['name'])}" if g else None

    def _check_unique(self):
        seen = {}
        for name, coll in self.coll.items():
            if coll in seen:
                raise SystemExit(f"Collection name clash: {name} and {seen[coll]} -> {coll}")
            seen[coll] = name

    # -- allowed children for bloks ------------------------------------------
    def allowed_blocks(self, f: dict) -> list[str]:
        nestable = [c for c in self.components if c.get("is_nestable")]
        if not f.get("restrict_components"):
            pool = nestable
        else:
            rtype = f.get("restrict_type") or ""
            if rtype == "groups":
                wl = set(f.get("component_group_whitelist") or [])
                pool = [c for c in nestable if c.get("component_group_uuid") in wl]
            elif rtype == "tags":
                wl = set(f.get("component_tag_whitelist") or [])
                pool = [c for c in nestable if wl & set(c.get("internal_tag_ids") or [])]
            else:
                order = f.get("component_whitelist") or []
                pool = [self.by_name[n] for n in order if n in self.by_name]
        deny = set(f.get("component_denylist") or [])
        gdeny = set(f.get("component_group_denylist") or [])
        pool = [c for c in pool if c["name"] not in deny and c.get("component_group_uuid") not in gdeny]
        names = [self.coll[c["name"]] for c in pool]
        if not f.get("restrict_components") or (f.get("restrict_type") in ("groups", "tags")):
            names = sorted(names)
        return names

    # -- build ---------------------------------------------------------------
    def build(self):
        used_groups = {c.get("component_group_uuid") for c in self.components}
        ordered = sorted(
            (g for g in self.groups.values() if g["uuid"] in used_groups),
            key=lambda g: (GROUP_ORDER.index(g["name"]) if g["name"] in GROUP_ORDER else 99, g["name"]),
        )
        for i, g in enumerate(ordered):
            self.folders.append({
                "collection": f"folder_{snake(g['name'])}",
                "schema": None,
                "meta": {
                    "icon": GROUP_ICONS.get(g["name"], "folder"),
                    "sort": i + 1,
                    "collapse": "open" if g["name"] in ("Pages", "Entities", "Global") else "closed",
                    "translations": tr(g["name"], g["name"], g["name"]),
                    "note": f"Storyblok component group “{g['name']}”",
                },
            })
        self.folders.append({
            "collection": "folder_junctions",
            "schema": None,
            "meta": {"icon": "link", "sort": 99, "hidden": True, "collapse": "closed",
                     "translations": tr("Junctions (bloks)", "Junction", "Junctions"),
                     "note": "Many-to-any junction tables backing Storyblok `bloks` fields"},
        })

        sorted_components = sorted(self.components, key=lambda c: (not self.is_story(c), c["name"].lower()))
        for idx, c in enumerate(sorted_components):
            self.collections.append(self.build_collection(c, idx))

    def build_collection(self, c: dict, idx: int) -> dict:
        coll = self.coll[c["name"]]
        story = self.is_story(c)
        reserved = STORY_FIELDS if story else BLOCK_FIELDS
        schema: dict = c.get("schema") or {}

        renames = {}
        for key, f in schema.items():
            if f.get("type") != "tab" and key in reserved:
                renames[key] = RENAME_PREFIX + key
                self.report.append(f"rename  {c['name']}.{key} -> {coll}.{renames[key]} (clashes with system field)")

        # tabs -> group fields
        group_of: dict[str, str] = {}
        tab_fields = []
        used_names = set(renames.get(k, k) for k, f in schema.items() if f.get("type") != "tab") | reserved
        for key, f in schema.items():
            if f.get("type") != "tab":
                continue
            label = f.get("display_name") or "Tab"
            gname = "tab_" + snake(label)
            n = 2
            while gname in used_names:
                gname = f"tab_{snake(label)}_{n}"
                n += 1
            used_names.add(gname)
            for k in f.get("keys") or []:
                if k in schema and k not in group_of:
                    group_of[k] = gname
            tab_fields.append({
                "field": gname, "type": "alias", "schema": None,
                "meta": {
                    "interface": "group-detail", "special": ["alias", "no-data", "group"],
                    "options": {"start": "closed", "headerIcon": "tab"},
                    "translations": tr(label), "sort": 1000 + (f.get("pos") or 0), "width": "full",
                },
            })

        fields: list[dict] = [{
            "field": "id", "type": "uuid",
            "meta": {"special": ["uuid"], "interface": "input", "readonly": True, "hidden": True, "sort": 1},
            "schema": {"is_primary_key": True, "length": 36, "has_auto_increment": False},
        }]
        if story:
            fields += self.story_fields(required=not c.get("is_nestable"))
            for uf in ("user_created", "user_updated"):
                self.relations.append({"collection": coll, "field": uf, "related_collection": "directus_users",
                                       "meta": {"sort_field": None}, "schema": {}})
        fields += tab_fields

        for key, f in sorted(schema.items(), key=lambda kv: kv[1].get("pos") or 0):
            t = f.get("type")
            if t == "tab":
                continue
            name = renames.get(key, key)
            out = self.convert_field(c, coll, key, name, f)
            for fld in out:
                fld["meta"]["sort"] = 100 + (f.get("pos") or 0)
                if key in group_of:
                    fld["meta"]["group"] = group_of[key]
                if name != key and fld["field"] == name and not fld["meta"].get("translations"):
                    fld["meta"]["translations"] = tr(title(key))
            fields += out

        group = c.get("component_group_uuid")
        g = self.groups.get(group or "")
        tags = [self.tags.get(t, str(t)) for t in (c.get("internal_tag_ids") or [])]
        kind = ("content type + nestable" if c.get("is_nestable") else "content type") if story else "nestable block"
        note = (c.get("description") or "").strip()
        note = (note + " · " if note else "") + f"Storyblok `{c['name']}` ({kind})" + (f", tags: {', '.join(tags)}" if tags else "")
        display = c.get("display_name") or title(c["name"])

        meta = {
            "icon": GROUP_ICONS.get(g["name"], "widgets") if g else ("article" if story else "widgets"),
            "color": c.get("color"),
            "note": note,
            "display_template": self.display_template(c, renames, story),
            "translations": tr(display, display, display),
            "group": self.folder_key(group),
            "sort": idx + 1,
            "accountability": "all",
        }
        if story:
            meta.update({"archive_field": "status", "archive_value": "archived", "unarchive_value": "draft",
                         "archive_app_filter": True})
        return {"collection": coll, "meta": clean(meta), "schema": {}, "fields": fields}

    def story_fields(self, required: bool) -> list[dict]:
        return [
            {"field": "status", "type": "string", "schema": {"default_value": "draft", "is_nullable": False},
             "meta": {"interface": "select-dropdown", "display": "labels", "width": "half", "sort": 2,
                      "options": {"choices": [
                          {"text": "Published", "value": "published", "color": "#2ECDA7"},
                          {"text": "Draft", "value": "draft", "color": "#A2B5CD"},
                          {"text": "Archived", "value": "archived", "color": "#F7971C"}]},
                      "display_options": {"showAsDot": True, "choices": [
                          {"text": "Published", "value": "published", "foreground": "#FFFFFF", "background": "#2ECDA7"},
                          {"text": "Draft", "value": "draft", "foreground": "#18222F", "background": "#A2B5CD"},
                          {"text": "Archived", "value": "archived", "foreground": "#FFFFFF", "background": "#F7971C"}]},
                      "note": "Storyblok publish state"}},
            {"field": "name", "type": "string", "schema": {},
             "meta": {"interface": "input", "width": "half", "sort": 3, "required": required,
                      "translations": tr("Story name"), "note": "Storyblok story name"}},
            {"field": "slug", "type": "string", "schema": {},
             "meta": {"interface": "input", "width": "full", "sort": 4,
                      "options": {"slug": True, "iconLeft": "link", "placeholder": "full/slug/path"},
                      "note": "Storyblok full_slug"}},
            {"field": "date_created", "type": "timestamp", "schema": {},
             "meta": {"special": ["date-created"], "interface": "datetime", "readonly": True, "hidden": True,
                      "width": "half", "display": "datetime", "display_options": {"relative": True}, "sort": 5}},
            {"field": "date_updated", "type": "timestamp", "schema": {},
             "meta": {"special": ["date-updated"], "interface": "datetime", "readonly": True, "hidden": True,
                      "width": "half", "display": "datetime", "display_options": {"relative": True}, "sort": 6}},
            {"field": "user_created", "type": "uuid", "schema": {},
             "meta": {"special": ["user-created"], "interface": "select-dropdown-m2o", "readonly": True,
                      "hidden": True, "width": "half", "display": "user", "sort": 7}},
            {"field": "user_updated", "type": "uuid", "schema": {},
             "meta": {"special": ["user-updated"], "interface": "select-dropdown-m2o", "readonly": True,
                      "hidden": True, "width": "half", "display": "user", "sort": 8}},
        ]

    def display_template(self, c: dict, renames: dict, story: bool) -> str | None:
        if story:
            return "{{name}}"
        schema = c.get("schema") or {}
        pf = c.get("preview_field")
        if pf and schema.get(pf, {}).get("type") in ("text", "textarea", "option"):
            return "{{" + renames.get(pf, pf) + "}}"
        for want_required in (True, False):
            for key, f in sorted(schema.items(), key=lambda kv: kv[1].get("pos") or 0):
                if f.get("type") == "text" and bool(f.get("required")) == want_required:
                    return "{{" + renames.get(key, key) + "}}"
        return None

    # -- field conversion -----------------------------------------------------
    def base_meta(self, f: dict, extra_note: str = "") -> dict:
        note = (f.get("description") or "").strip()
        if extra_note:
            note = (note + " · " if note else "") + extra_note
        return clean({
            "required": bool(f.get("required")) or None,
            "note": note or None,
            "translations": tr(f.get("display_name")),
            "width": "full",
        })

    def convert_field(self, comp: dict, coll: str, key: str, name: str, f: dict) -> list[dict]:
        t = f.get("type")
        meta = self.base_meta(f)
        dv = f.get("default_value")

        if t == "text":
            if f.get("field_type") == "storyblok-colorpicker":
                meta.update(interface="select-color", display="color")
                return [{"field": name, "type": "string", "schema": {"max_length": 32}, "meta": meta}]
            ml = to_num(f.get("max_length"), False)
            schema = {"default_value": dv if dv not in (None, "") else None}
            ftype = "string"
            if ml and ml > 255:
                ftype = "text"
            elif ml:
                schema["max_length"] = ml
            meta.update(interface="input", options={"trim": True})
            if f.get("regex"):
                meta["validation"] = {"_and": [{name: {"_regex": f["regex"]}}]}
                meta["validation_message"] = f"Must match Storyblok regex {f['regex']}"
            return [{"field": name, "type": ftype, "schema": clean(schema), "meta": meta}]

        if t == "textarea":
            meta.update(interface="input-multiline")
            return [{"field": name, "type": "text", "schema": {}, "meta": meta}]

        if t == "markdown":
            meta.update(interface="input-rich-text-md")
            return [{"field": name, "type": "text", "schema": {}, "meta": meta}]

        if t == "richtext":
            toolbar = DEFAULT_TOOLBAR
            if f.get("customize_toolbar") and f.get("toolbar"):
                toolbar = []
                for item in f["toolbar"]:
                    for m in TOOLBAR_MAP.get(item, []):
                        if m not in toolbar:
                            toolbar.append(m)
                toolbar.append("fullscreen")
            meta.update(interface="input-rich-text-html", display="formatted-value",
                        options={"toolbar": toolbar}, display_options={"format": True})
            return [{"field": name, "type": "text", "schema": {}, "meta": meta}]

        if t == "number":
            dec = to_num(f.get("decimals"), False) or 0
            step = f.get("steps")
            is_float = dec > 0 or (step is not None and float(step) % 1 != 0)
            opts = clean({"min": to_num(f.get("min_value"), is_float), "max": to_num(f.get("max_value"), is_float),
                          "step": to_num(step, True) if step is not None else None})
            meta.update(interface="input", options=opts or None)
            meta = clean(meta)
            return [{"field": name, "type": "float" if is_float else "integer",
                     "schema": clean({"default_value": to_num(dv, is_float)}), "meta": meta}]

        if t == "boolean":
            meta.update(interface="boolean", display="boolean", width="half",
                        options=clean({"label": f.get("display_name") or title(key)}) if f.get("inline_label") else None,
                        special=["cast-boolean"])
            meta = clean(meta)
            return [{"field": name, "type": "boolean",
                     "schema": {"default_value": bool(to_bool(dv)) if dv is not None else False}, "meta": meta}]

        if t == "datetime":
            date_only = bool(f.get("disable_time"))
            meta.update(interface="datetime", display="datetime", width="half")
            return [{"field": name, "type": "date" if date_only else "timestamp", "schema": {}, "meta": meta}]

        if t == "asset":
            ft = f.get("filetypes") or []
            image = ft == ["images"]
            extra = f"Storyblok filetypes: {', '.join(ft)}" if ft and not image else ""
            meta = self.base_meta(f, extra)
            meta.update(interface="file-image" if image else "file", display="image" if image else "file",
                        special=["file"], width="half")
            self.relations.append({"collection": coll, "field": name, "related_collection": "directus_files",
                                   "meta": {"sort_field": None}, "schema": {"on_delete": "SET NULL"}})
            return [{"field": name, "type": "uuid", "schema": {}, "meta": meta}]

        if t in ("option", "options") and f.get("source") == "internal_stories":
            return self.story_reference(comp, coll, key, name, f)

        if t == "option":
            choices = [{"text": o.get("name") or str(o.get("value")), "value": str(o.get("value"))}
                       for o in (f.get("options") or [])]
            meta.update(interface="select-dropdown", display="labels" if choices else None,
                        options={"choices": choices, "allowNone": not f.get("exclude_empty_option")},
                        display_options={"choices": choices} if choices else None, width="half")
            meta = clean(meta)
            return [{"field": name, "type": "string", "schema": clean({"default_value": dv or None}), "meta": meta}]

        if t == "options":
            choices = [{"text": o.get("name") or str(o.get("value")), "value": str(o.get("value"))}
                       for o in (f.get("options") or [])]
            meta.update(interface="select-multiple-dropdown", special=["cast-json"], display="labels",
                        options={"choices": choices}, display_options={"choices": choices})
            return [{"field": name, "type": "json", "schema": {}, "meta": meta}]

        if t == "multilink":
            meta = self.base_meta(f, "Storyblok multilink (URL or /internal/slug)")
            meta.update(interface="input", options={"iconLeft": "link", "placeholder": "https://… or /full/slug",
                                                    "trim": True})
            out = [{"field": name, "type": "string", "schema": {"max_length": 2048}, "meta": meta}]
            if f.get("allow_target_blank"):
                out.append({"field": f"{name}_target_blank", "type": "boolean", "schema": {"default_value": False},
                            "meta": {"interface": "boolean", "special": ["cast-boolean"], "width": "half",
                                     "options": {"label": "Open in new tab"},
                                     "translations": tr(f"{f.get('display_name') or title(key)} – new tab")}})
            return out

        if t == "bloks":
            return self.bloks_field(comp, coll, key, name, f)

        if t == "custom":
            meta = self.base_meta(f, f"Storyblok custom field plugin `{f.get('field_type')}`")
            meta.update(interface="input", options={"iconLeft": "mood"} if "emoji" in (f.get("field_type") or "") else None)
            meta = clean(meta)
            return [{"field": name, "type": "string", "schema": {}, "meta": meta}]

        self.report.append(f"WARN    unknown type '{t}' on {comp['name']}.{key} -> stored as json")
        meta.update(interface="input-code", options={"language": "JSON"}, special=["cast-json"])
        return [{"field": name, "type": "json", "schema": {}, "meta": meta}]

    def story_reference(self, comp, coll, key, name, f) -> list[dict]:
        targets = []
        for ct in f.get("filter_content_type") or []:
            target = self.by_lower.get(ct.lower())
            if target and self.coll[target["name"]] not in targets:
                targets.append(self.coll[target["name"]])
        max_opts = to_num(f.get("max_options"), False)
        single = f.get("type") == "option" or max_opts == 1

        if len(targets) == 1 and single:
            meta = self.base_meta(f, f"Storyblok story reference → {targets[0]}")
            meta.update(interface="select-dropdown-m2o", special=["m2o"], display="related-values",
                        options={"template": "{{name}}"}, display_options={"template": "{{name}}"})
            self.relations.append({"collection": coll, "field": name, "related_collection": targets[0],
                                   "meta": {"sort_field": None}, "schema": {"on_delete": "SET NULL"}})
            return [{"field": name, "type": "uuid", "schema": {}, "meta": meta}]

        # Untyped / multi-type story reference -> many-to-any over story collections.
        if not targets:
            folder = (f.get("folder_slug") or "").strip("/")
            pool = [c for c in self.components if self.is_story(c)]
            if folder:
                gl = [c for c in pool if (self.groups.get(c.get("component_group_uuid") or "") or {}).get("name", "").lower() == folder.lower()]
                pool = gl or pool
            targets = sorted(self.coll[c["name"]] for c in pool)
        extra = {"maximum": max_opts or (1 if single else None), "note": f"Storyblok story reference (folder: {f.get('folder_slug') or 'any'})"}
        self.report.append(f"m2a     {comp['name']}.{key}: polymorphic story reference -> M2A over {len(targets)} collections")
        return self.m2a(comp, coll, key, name, f, targets, extra, select_existing=True)

    def bloks_field(self, comp, coll, key, name, f) -> list[dict]:
        allowed = self.allowed_blocks(f)
        return self.m2a(comp, coll, key, name, f, allowed,
                        {"minimum": f.get("minimum"), "maximum": f.get("maximum")}, select_existing=False)

    def m2a(self, comp, coll, key, name, f, allowed, limits, select_existing) -> list[dict]:
        junction = f"{coll}_{snake(name)}"
        taken = set(self.coll.values()) | {j["collection"] for j in self.junctions}
        n = 2
        while junction in taken:
            junction = f"{coll}_{snake(name)}_{n}"
            n += 1
        fk = f"{coll}_id"
        if len(fk) > 60:
            fk = fk[:57] + "_id"
        lim = []
        if limits.get("minimum"):
            lim.append(f"min {limits['minimum']}")
        if limits.get("maximum"):
            lim.append(f"max {limits['maximum']}")
        extra = ("Storyblok limits: " + ", ".join(lim)) if lim else ""
        if limits.get("note"):
            extra = (limits["note"] + (" · " + extra if extra else ""))
        self.m2a_info[(coll, name)] = {
            "allowed": allowed, "min": to_num(limits.get("minimum"), False),
            "max": to_num(limits.get("maximum"), False), "link_existing": select_existing,
            "required": bool(f.get("required")),
        }
        meta = self.base_meta(f, extra)
        meta.update(interface="list-m2a", special=["m2a"], display="related-values",
                    options={"enableSelect": select_existing, "enableCreate": True, "enableLink": select_existing})
        self.junctions.append({
            "collection": junction,
            "schema": {},
            "meta": {"hidden": True, "icon": "import_export", "group": "folder_junctions",
                     "note": f"Junction for {coll}.{name} (Storyblok bloks)"},
            "fields": [
                {"field": "id", "type": "integer", "schema": {"is_primary_key": True, "has_auto_increment": True},
                 "meta": {"hidden": True}},
                {"field": fk, "type": "uuid", "schema": {}, "meta": {"hidden": True}},
                {"field": "item", "type": "string", "schema": {}, "meta": {"hidden": True}},
                {"field": "collection", "type": "string", "schema": {}, "meta": {"hidden": True}},
                {"field": "sort", "type": "integer", "schema": {}, "meta": {"hidden": True}},
            ],
        })
        self.relations.append({
            "collection": junction, "field": fk, "related_collection": coll,
            "meta": {"one_field": name, "sort_field": "sort", "one_deselect_action": "delete", "junction_field": "item"},
            "schema": {"on_delete": "CASCADE"},
        })
        self.relations.append({
            "collection": junction, "field": "item", "related_collection": None,
            "meta": {"one_allowed_collections": allowed, "one_collection_field": "collection",
                     "junction_field": fk, "one_deselect_action": "nullify"},
            "schema": None,
        })
        return [{"field": name, "type": "alias", "schema": None, "meta": meta}]

    def as_dict(self) -> dict:
        return {"folders": self.folders, "collections": self.collections, "junctions": self.junctions,
                "relations": self.relations, "report": self.report}


# --------------------------------------------------------------------------- API

class Directus:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token

    def req(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.url + path, data=data, method=method)
        r.add_header("Content-Type", "application/json")
        if self.token:
            r.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {msg[:600]}") from None

    @classmethod
    def login(cls, url: str, email: str, password: str) -> "Directus":
        d = cls(url, "")
        res = d.req("POST", "/auth/login", {"email": email, "password": password})
        return cls(url, res["data"]["access_token"])


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def connect() -> Directus:
    load_env()
    url = os.environ.get("DIRECTUS_URL", "http://localhost:8055")
    token = os.environ.get("DIRECTUS_TOKEN")
    if token:
        return Directus(url, token)
    email, pw = os.environ.get("DIRECTUS_EMAIL"), os.environ.get("DIRECTUS_PASSWORD")
    if email and pw:
        return Directus.login(url, email, pw)
    raise SystemExit("Set DIRECTUS_TOKEN (or DIRECTUS_EMAIL + DIRECTUS_PASSWORD) in env or .env")


def apply(api: Directus, plan: dict):
    errors = []
    existing = {c["collection"] for c in api.req("GET", "/collections")["data"]}

    def create_collection(payload: dict):
        name = payload["collection"]
        if name not in existing:
            api.req("POST", "/collections", payload)
            existing.add(name)
            print(f"  + collection {name}")
            return
        if payload.get("schema") is None:
            return
        have = {f["field"] for f in api.req("GET", f"/fields/{name}")["data"]}
        for fld in payload.get("fields", []):
            if fld["field"] not in have:
                api.req("POST", f"/fields/{name}", fld)
                print(f"  + field {name}.{fld['field']}")

    for stage in ("folders", "collections", "junctions"):
        print(f"\n== {stage} ({len(plan[stage])})")
        for payload in plan[stage]:
            try:
                create_collection(payload)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                print(f"  ! {payload['collection']}: {e}")

    print(f"\n== relations ({len(plan['relations'])})")
    have_rel = {(r["collection"], r["field"]) for r in api.req("GET", "/relations")["data"]}
    for rel in plan["relations"]:
        k = (rel["collection"], rel["field"])
        if k in have_rel:
            continue
        try:
            api.req("POST", "/relations", rel)
            have_rel.add(k)
            print(f"  + relation {k[0]}.{k[1]} -> {rel['related_collection'] or 'm2a'}")
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
            print(f"  ! relation {k[0]}.{k[1]}: {e}")

    print(f"\nDone with {len(errors)} error(s).")
    return errors


def reset(api: Directus, plan: dict):
    existing = {c["collection"] for c in api.req("GET", "/collections")["data"]}
    order = plan["junctions"] + plan["collections"] + plan["folders"]
    for payload in order:
        name = payload["collection"]
        if name in existing:
            try:
                api.req("DELETE", f"/collections/{name}")
                print(f"  - {name}")
            except Exception as e:  # noqa: BLE001
                print(f"  ! {name}: {e}")


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schema-dir", default=str(SCHEMA_DIR))
    ap.add_argument("--dry-run", action="store_true", help="only write the plan JSON")
    ap.add_argument("--plan-out", default=str(ROOT / "scripts" / "directus-schema-plan.json"))
    ap.add_argument("--reset", action="store_true", help="delete all collections this script manages")
    ap.add_argument("--yes", action="store_true", help="confirm --reset")
    args = ap.parse_args()

    sd = Path(args.schema_dir)
    components = json.loads((sd / "components.json").read_text())
    groups = json.loads((sd / "groups.json").read_text())
    tags = json.loads((sd / "tags.json").read_text()) if (sd / "tags.json").exists() else []

    planner = Planner(components, groups, tags)
    planner.build()
    plan = planner.as_dict()
    Path(args.plan_out).write_text(json.dumps(plan, indent=2, ensure_ascii=False))

    n_fields = sum(len(c["fields"]) for c in plan["collections"])
    print(f"Plan: {len(plan['folders'])} folders, {len(plan['collections'])} collections "
          f"({n_fields} fields), {len(plan['junctions'])} junctions, {len(plan['relations'])} relations")
    print(f"Plan written to {args.plan_out}")
    for line in plan["report"]:
        print("  " + line)

    if args.dry_run:
        return
    api = connect()
    if args.reset:
        if not args.yes:
            raise SystemExit("--reset deletes collections AND their data; re-run with --yes to confirm")
        reset(api, plan)
        return
    errors = apply(api, plan)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

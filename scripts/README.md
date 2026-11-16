# Storyblok → Directus demo import

```bash
cp .env.example .env && chmod 600 .env    # put DIRECTUS_TOKEN (or EMAIL/PASSWORD) in it
python3 scripts/storyblok_to_directus.py   # 1. schema (idempotent)
python3 scripts/seed_dummy_data.py         # 2. dummy content (idempotent)
```

- `--dry-run` on either script writes the plan / data JSON without calling Directus
  (`directus-schema-plan.json`, `seed-data.json`).
- `python3 scripts/storyblok_to_directus.py --reset --yes` removes every collection the
  schema script created, **including its data**. Run this before starting over.

## Mapping

| Storyblok | Directus |
|---|---|
| Content type (`is_root`) | Collection `<name>` + `name`, `slug`, `status` (draft/published/archived), created/updated |
| Nestable block | Collection `block_<name>` |
| Component group | Collection folder |
| `bloks` (+ whitelist / group whitelist) | Many-to-any (`list-m2a`), allowed collections = resolved whitelist; min/max in field note |
| `option`/`options` → internal story | Many-to-one to that content type (polymorphic → M2A, link existing) |
| `asset` | File (image) relation to `directus_files` |
| `richtext` | HTML WYSIWYG, toolbar mapped |
| `option` inline | Dropdown with the same choices/default |
| `multilink` | URL string (+ `<field>_target_blank` toggle) |
| `tab` | Collapsible field group |
| text regex / max_length, number min/max/step, required | Field validation / length / interface options / required |

Field keys keep their Storyblok spelling (camelCase included). Keys that clash with
system fields are prefixed `sb_` (e.g. `prize.id` → `prize.sb_id`).

Not carried over: presets and workflows/roles (they are not part of the export), and
the exact block-count limits, which Directus can't enforce on many-to-any fields.

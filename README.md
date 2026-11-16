# Storyblok → Directus demo

A local Directus instance that recreates a Storyblok space's content model (125 components, 661 fields)
and fills it with dummy content. Use it to see how a Storyblok setup looks and behaves in Directus.

## What's in here

| Path | What it is |
|---|---|
| `docker-compose.yml` | Directus 12.6.1 + Postgres (PostGIS) + Redis |
| `storyblok-schema/` | The Storyblok export the demo is built from (see its [README](storyblok-schema/README.md)) |
| `scripts/storyblok_to_directus.py` | Turns `components.json` into Directus collections, fields and relations |
| `scripts/seed_dummy_data.py` | Fills the schema with German dummy content and placeholder images |
| `.env.example` | Template for the credentials the scripts use |

The scripts use only the Python standard library, so there's nothing to install.

## Quick start

Requires Docker and Python 3.9+.

```bash
# 1. Start Directus (first boot takes a minute)
docker compose up -d
#    → http://localhost:8055, log in with ADMIN_EMAIL / ADMIN_PASSWORD from docker-compose.yml

# 2. Give the scripts access
cp .env.example .env && chmod 600 .env
#    Set DIRECTUS_TOKEN (admin user → Token → generate, then save the user),
#    or set DIRECTUS_EMAIL + DIRECTUS_PASSWORD instead

# 3. Create the schema, then the content
python3 scripts/storyblok_to_directus.py
python3 scripts/seed_dummy_data.py
```

Both scripts are idempotent: running them again skips anything that already exists.

| Command | Effect |
|---|---|
| `… --dry-run` (either script) | Write `scripts/directus-schema-plan.json` / `scripts/seed-data.json` without calling Directus |
| `python3 scripts/storyblok_to_directus.py --reset --yes` | Delete every collection the import created, **data included** |
| `docker compose down && rm -rf data uploads/*` | Wipe the whole instance and start fresh |

## What you get in Directus

- **Content types** (`season_page`, `prize`, `footer`, …): collections with story fields: `name`, `slug` and `status` (draft / published / archived).
- **Blocks:** `block_*` collections, grouped into folders matching the Storyblok component groups (Pages, Entities, Global, Blocks, …).
- **Nested blocks (`bloks`):** many-to-any fields, limited to the same components Storyblok allows, so pages are built from blocks as in Storyblok.
- **References:** story references are relations, assets live in the Directus file library, and rich text uses the WYSIWYG editor.
- **Field setup:** dropdown choices, defaults, regex validation, required flags and tabs are carried over.
- **Dummy data:** about 70 stories with roughly 190 nested blocks. Every block type appears at least once; the draft page *Block Showcase* collects the rest.

The full field-by-field mapping and its limitations are in [scripts/README.md](scripts/README.md). The main differences from Storyblok:

- Directus can't enforce the min/max block counts, so they are shown in the field notes.
- Links are a URL field plus a "new tab" toggle, not Storyblok's internal-link picker.
- Fields named `id` or `name` that clash with system fields are renamed `sb_id` / `sb_name`.
- Presets, workflows and roles are not part of the export, so they are not recreated.

## Privacy & security

- **No personal data.** The repo holds the content *model* only, with no real content. All dummy content is fictional:
  placeholder names (Max Mustermann, …), `example.com` links, fake video IDs and generated images. Don't import
  real content (winners, customer numbers, testimonials) into a shared demo instance: that is personal data
  under GDPR.
- **Anonymised export.** The Storyblok export was stripped of identifying details: domain, partner and vendor
  names, marketing copy, asset filenames, internal IDs and timestamps. See `storyblok-schema/README.md`.
- **Local only.** The admin password and `SECRET` in `docker-compose.yml` are placeholders; change them before running
  this anywhere else. `.env`, the database (`data/`) and uploaded files are git-ignored.

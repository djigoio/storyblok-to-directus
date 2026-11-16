# Storyblok schema export (DCR)

Export of our Storyblok component schema, shared to evaluate consistency/migration with Directus. Generated from `storyblok components pull`; space ID and asset thumbnails are redacted.

**Anonymised for this demo repo:**
- The domain, partner/sponsor and vendor names, and brand colour names are replaced with neutral ones.
  A few field keys and option values change as a result: `hideReviewScore`, `partnerMagazine`, `anthracite`.
- Preset copy and asset filenames are placeholders.
- Storyblok numeric IDs and UUIDs are remapped consistently, so references between files still resolve.
- Timestamps are normalised and asset folder IDs removed.

The structure, field types, validations and nesting rules are unchanged.

## Files

| File | Content |
|---|---|
| `components.json` | **Source of truth.** All components with fields, types, validations, nesting rules |
| `groups.json` | Component groups (folders in the Storyblok UI) |
| `presets.json` | Saved default values for components |
| `tags.json` | Internal component tags |
| `optional-typescript-types/` | TypeScript types generated from `components.json` (derived; for reference only) |

## At a glance

- **125 components**: 35 content types (root, i.e. a page/story type) and 109 nestable blocks (a few can be both).
- **661 fields** in total, 276 marked required.
- Field types: text (279), boolean (102), asset (71), option (69), bloks (46), richtext (44), multilink (23), options (11), number (8), datetime (5), textarea (2), custom (1); plus 9 UI tabs (layout only).
- **Nesting**: 46 `bloks` fields (nested blocks, the Storyblok way of composing pages); 41 restrict allowed children via whitelist, 5 accept any component.
- **Datasources**: none referenced by the schema (no `datasource_slug` fields); `options` fields use inline values or internal story references.
- One custom field plugin (`emoji-picker`, stored as a plain string).

## Components per group

| Group | Components |
|---|---|
| Blocks | 57 |
| Block Children | 27 |
| Global | 12 |
| Season Page Blocks | 9 |
| App Blocks | 6 |
| Pages | 5 |
| Entities | 5 |
| House Blocks | 2 |
| (none) | 2 |

## Content types (root components)

| Component | Fields | Nested-block fields | Group |
|---|---|---|---|
| `announcement_banner` | 3 | - | Global |
| `appCharity` | 4 | - | App Blocks |
| `backgroundColorBlok` | 2 | children | Blocks |
| `contentPage` | 6 | content_bloks | Pages |
| `experiment` | 2 | variant_bloks | Blocks |
| `experiment-variant` | 2 | variant_blok | Block Children |
| `faqs` | 5 | faqs | Blocks |
| `featureList` | 6 | features | Blocks |
| `footer` | 7 | links, socialMediaLinks | Global |
| `imageWithText` | 17 | - | Blocks |
| `intermediatePrize` | 7 | - | Blocks |
| `intermediatePrizeGallery` | 5 | - | Blocks |
| `intermediate_prize_story` | 29 | imageGallery | Block Children |
| `knownFrom` | 3 | logos | Blocks |
| `landingPageFooter` | 5 | socialMediaLinks, links | Global |
| `linkWithImage` | 4 | - | Blocks |
| `media` | 2 | - | Entities |
| `metadata` | 2 | - | Global |
| `minimalFooter` | 2 | links | Global |
| `packageSelection` | 3 | - | Blocks |
| `prize` | 17 | - | Entities |
| `prize_page` | 6 | intro_block, blocks | Pages |
| `redirect_page` | 1 | - | Pages |
| `root` | 1 | - | - |
| `scheduleBlok` | 3 | children | Blocks |
| `season` | 8 | - | Entities |
| `season_page` | 6 | stage_block, blocks | Pages |
| `season_pass` | 4 | - | Entities |
| `season_pass_page` | 6 | stage_block, blocks | Pages |
| `share_button` | 4 | - | Blocks |
| `specialDrawOffer` | 12 | - | Blocks |
| `splitText` | 5 | - | Blocks |
| `technical_data` | 1 | technical_data_items | Entities |
| `text_video_block` | 13 | - | Blocks |
| `winnerTestimonials` | 4 | testimonials | Global |

## Notes for the Directus team

- Storyblok `bloks` (nested components, often polymorphic) is the main modelling difference: it maps to a many-to-any / JSON-block structure in Directus rather than plain relations.
- `components.json` covers schema only. Content volume, languages/translation setup, workflows and roles are not part of it; we can describe them on request.
- Field-level details worth reading: `required`, `restrict_components` / `component_whitelist`, `options` (enum values), `min`/`max`, `regex`.

# Bluetooth Order safety rules

- Reorder only native paired-device React rows; no alternate device list.
- Use the shared `@decky/ui` external supplied by Decky. Do not initialize another module cache.
- No raw webpack require, numeric module identifiers, fake rendering, Bluetooth service mutation, signal-strength rewriting, or React prototype/global changes.
- No changes or installs in `/home/deck/homebrew/plugins` before the complete package passes tests and review.
- Keep native handlers, keys, focus navigation, discovery, and device data intact. Unknown layouts fail open.
- User-approved exception: attach only verified QAM memo renderer `type` references and matching alternate using Decky's own `getReactRoot`/`findInReactTree` pattern. Preserve originals, restore owned references after unpatch, and never reset roots or edit other fiber fields.
- Initial QAM and Settings overrides are off. Test separately before enabling both.
- Run the pinned Ponytail development/review process: correctness, safety, load, tests, speed, lean.
- Never commit device identities, logs, credentials, or personal diagnostics. Publishing waits for user authentication and shared verification.
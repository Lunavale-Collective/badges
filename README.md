# Lunavale Badges

This is a public repository used to render and update Lunavale badges outside of the private project repositories.

Keeping badge assets and badge update workflows here lets public profile pages, documentation, and status displays reference stable public URLs without exposing private roadmap, service, game, or planning repositories.

## Release Badge Data

Release timeline badges are generated into `.badges/releases/` as Shields endpoint JSON.

Examples:

- `https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Lunavale-Collective/badges/main/.badges/releases/internal-alpha.json`
- `https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Lunavale-Collective/badges/main/.badges/releases/internal-beta.json`
- `https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Lunavale-Collective/badges/main/.badges/releases/public-beta.json`
- `https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Lunavale-Collective/badges/main/.badges/releases/public-early-release.json`
- `https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Lunavale-Collective/badges/main/.badges/releases/public-release.json`

The update workflow checks GitHub issues first for matching roadmap IDs, then falls back to `scripts-roadmap/data/issues/<id>.yml`.

Because the roadmap and issue repositories are private, configure a repository secret named `ROADMAP_BADGE_TOKEN` with read access to the private roadmap/issues and write access is not required for those private repos. The script can also use `GH_TOKEN` if that secret already exists. The workflow's normal `GITHUB_TOKEN` is only used to commit generated JSON back to this public badge repo.

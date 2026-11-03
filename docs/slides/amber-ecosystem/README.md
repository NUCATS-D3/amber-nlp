# The amber projects: overview deck

A 14-slide introduction to amber, copal, lapidary, and loupe: the vision, what each
project owns, and how they fit together. Intended for clinical informatics and research
colleagues; about 15 minutes.

- `deck.md`: Marp source (default theme with inline styles).
- `assets/ecosystem.svg`, `assets/loop.svg`: the dependency and workflow diagrams.

The note excerpt on slide 4 is invented. The deck contains no clinical data and claims no
clinical performance results. The status on slide 13 reflects each repository as of
2026-11-02.

## Build

```sh
npx --yes @marp-team/marp-cli@4.3.1 deck.md --html --allow-local-files -o deck.html
npx --yes @marp-team/marp-cli@4.3.1 deck.md --html --allow-local-files --pdf -o deck.pdf
```

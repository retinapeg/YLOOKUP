# FundOps Control Room (YLOOKUP)

A hackathon build that checks a fund's capital-call notice against its investor register: a model may read the notice, but code does the arithmetic and a person clears every break.

**Result:** On a 27-case synthetic test set, the rule-based path extracted 267 of 270 fields exactly and found all 12 gold exceptions, with no model calls.

**Status:** Hackathon prototype (September 2026). All data is fictional.

- In model mode, a field is kept only if its quoted evidence actually appears on the page it cites.
- Model mode is unevaluated, and the reviewer misses issues that need more than one field's context.

[Technical details →](docs/GUIDE.md)

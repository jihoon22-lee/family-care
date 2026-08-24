# ADR 0006: Permissive PDF parser stack

- Status: Accepted
- Date: 2026-08-24

## Context

Phase 1 needs deterministic text, word, coordinate, table, page, and encryption handling for synthetic PDF fixtures. The repository is currently public, has no `LICENSE`, and maintains a proprietary-distribution posture. The parser choice therefore has to fit the repository's current dependency and distribution boundary as an engineering decision.

The earlier ingestion design named PyMuPDF as the primary extractor. PyMuPDF uses an AGPL/commercial dual-license model. That model conflicts with the repository's current no-license/proprietary-distribution posture unless a future explicit license decision changes it. This ADR records the rejection for now; it is not legal advice.

## Decision

Use the following permissively licensed stack at the exact versions below:

- `pdfplumber==0.11.10` is the primary text, word, coordinate, and table/cell candidate extractor.
- `pypdf==6.16.2` performs structural, page-count, and encryption validation before extraction.
- `reportlab==5.0.1` generates deterministic synthetic PDF fixtures in tests.

The parser runs in a dedicated Linux child process with the Phase 1 resource limits. The parent opens the source once with descriptor-based no-follow semantics; the child receives only an inherited or duplicated read-only descriptor and canonical JSON settings, has no network client or external URL resolver, and never receives a queued password. The version, extractor configuration, and quality-rule version are persisted with the extraction result. Windows descriptor passing and resource-limit behavior remain unverified.

## Alternatives

### PyMuPDF

Rejected for Phase 1 because its AGPL/commercial dual-license model conflicts with the repository's current no-license/proprietary-distribution posture. A future explicit license decision may reopen this option through a new ADR.

### pypdf as the sole extractor

Rejected because structural parsing and encryption checks do not provide the required word coordinates and table/cell candidates with the chosen output contract.

### External conversion or OCR service

Rejected because Phase 1 must run without external providers, private-data transfer, network access, or non-deterministic service output. OCR execution is outside Phase 1.

### A permissive native parser not yet measured in the synthetic corpus

Deferred because Phase 1 needs a small, reproducible stack with explicit table and coordinate behavior. A replacement requires the same contract and regression corpus.

## Consequences

- Dependency versions and notices are reviewable in `uv.lock` and the Phase 1 `THIRD_PARTY_NOTICES.md` artifact created after these dependencies land.
- Synthetic extraction tests can run in public CI without real documents, external credentials, or external network access.
- `pdfplumber` behavior and `pypdf` structural behavior require separate regression tests because they own different parts of the contract.
- The repository does not claim that this engineering choice resolves licensing questions; future distribution changes require an explicit project decision and refreshed review.
- Actual private-data acceptance remains blocked until an approved runtime boundary, including production hardening for OS egress enforcement, exists.

## References

These upstream links record the engineering inputs to this decision; they are not legal advice:

- pdfplumber license: <https://github.com/jsvine/pdfplumber/blob/stable/LICENSE.txt>
- PyMuPDF repository: <https://github.com/pymupdf/PyMuPDF>
- PyMuPDF license statement: <https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright>
- pypdf project metadata: <https://pypi.org/project/pypdf/>
- reportlab project metadata: <https://pypi.org/project/reportlab/>

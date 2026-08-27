# Tolerate recoverable PDF structure deviations

## Overview

Worker PDF intake now accepts structurally readable PDFs whose cross-reference
pointer has a recoverable one-byte deviation. The regression is synthetic and
keeps encryption, page-count, corruption, descriptor, hashing, and privacy
boundaries unchanged.

## Changes made

- `workers/analyzer/src/familycare_worker/pdf/intake.py`: use
  `PdfReader(..., strict=False)` for structural validation so pypdf can repair
  recoverable producer deviations.
- `workers/analyzer/tests/test_pdf_intake.py`: generate a synthetic PDF, shift
  its `startxref` pointer by one, assert strict mode rejects it, and assert
  intake accepts it with the expected page count.

## Key code example

```python
with _duplicate_handle(source.fd) as handle:
    reader = PdfReader(handle, strict=False)
    if reader.is_encrypted:
        raise PasswordRequired
    page_count = len(reader.pages)
```

## Verification results

```text
Focused RED: regression failed with PDF_CORRUPT under strict=True
Focused GREEN: 62 focused PDF intake/extraction/password/isolation tests passed
git diff --check: passed
```

Only synthetic temporary PDFs were used. No real/private documents, external
paths, document text, credentials, or identifiers were accessed or recorded.

## Recoverable out-of-bounds extraction geometry

### Context

Some readable PDFs can produce an individual `pdfplumber` word, table, or cell
candidate whose bounding box extends just beyond the page boundary. The
coordinate contract remains strict, but one malformed candidate should not
discard otherwise usable candidates from the same page.

### Changes made

- `workers/analyzer/src/familycare_worker/pdf/extractor.py`: catch only the
  `PdfCorrupt` raised by per-word, per-table, and per-cell `normalize_bbox`
  calls. The invalid candidate is omitted; malformed collection/object
  structure still follows the existing failure path.
- `workers/analyzer/tests/test_pdf_extraction.py`: add synthetic fake-page,
  fake-table, and fake-cell regressions covering each item-level omission and
  preserving valid siblings and reading order.

The shared `normalize_bbox` implementation was not relaxed. Encryption,
descriptor isolation, page limits, resource limits, and cleanup behavior were
not changed.

### Verification results

```text
Focused RED: 3 new synthetic extraction tests failed with PDF_CORRUPT
Focused GREEN: 14 workers/analyzer/tests/test_pdf_extraction.py passed
Focused regression: 30 extraction and coordinate-quality tests passed
ruff format --check: passed (2 files)
ruff check: passed (2 files)
git diff --check: passed
```

The repository tests above used only synthetic fake objects and existing
synthetic temporary PDFs. No private document content, external path,
credential, or identifier was recorded.

### Private-runtime acceptance

The root agent then ran the rebuilt Worker parser in a disposable container
against the seven existing policy items whose latest sanitized failure was
`PDF_CORRUPT`. This was a read-only diagnostic: it did not requeue items or
write extraction results to the database.

- All 7 policy items extracted successfully.
- Documents ranged from 2 to 109 pages.
- Serialized results ranged from 305,833 to 12,767,612 bytes, below the
  existing 64 MiB parser-output limit.
- Per-item elapsed time ranged from 504 to 24,577 milliseconds, below the
  existing parser limits.

Each plaintext copy lived only in a mode-`0600` file beneath a disposable
temporary directory and was removed when the diagnostic finished. Output was
limited to ordinal numbers and aggregate timing, page-count, and size values;
no document text, family value, filename, path, database identifier, password,
or credential was emitted or added to Git.

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

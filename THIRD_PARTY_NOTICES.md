# Third-party notices for the synthetic PDF stack

This inventory records the exact parser and fixture packages resolved in
`uv.lock`. The versions below are the versions used by this repository, and
the URLs point to the corresponding upstream project or license source.

The repository has no `LICENSE` file and does not grant reuse permission by
default. This file is an inventory, not legal advice. If a future distribution
includes any of these packages, retain the upstream license and attribution
notices required by the package distributions.

## Direct packages

| Package | Version | Boundary | Declared license | Official source |
| --- | --- | --- | --- | --- |
| `cryptography` | `50.0.0` | Worker runtime; managed archive encryption and parser dependency | Apache-2.0 or BSD-3-Clause; contributions under both | [project](https://github.com/pyca/cryptography) |
| `pdfplumber` | `0.11.10` | Worker runtime | MIT | [project license](https://github.com/jsvine/pdfplumber/blob/stable/LICENSE.txt) |
| `pillow` | `12.3.0` | Worker runtime; secure PNG rendering and validation | MIT-CMU (Pillow License) | [project license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| `pypdf` | `6.16.2` | Worker runtime | BSD-3-Clause | [project license](https://github.com/py-pdf/pypdf/blob/main/LICENSE) |
| `pypdfium2` | `5.13.0` | Worker runtime; descriptor-derived local page rendering | Apache-2.0 and BSD-3-Clause; dependency licenses | [project](https://github.com/pypdfium2-team/pypdfium2) |
| `reportlab` | `5.0.1` | Development/test fixtures only; not a Worker runtime dependency | BSD-3-Clause | [project metadata](https://pypi.org/project/reportlab/) |

The Worker container also installs Debian's `tesseract-ocr`,
`tesseract-ocr-eng`, and `tesseract-ocr-kor` runtime packages. Tesseract and
the upstream trained-data repositories are distributed under Apache-2.0; the
package-provided notices remain authoritative for the exact Debian image
revision used at build time. No network OCR service or downloadable runtime
language pack is used.

## Resolved transitive packages

These packages are pulled by the direct parser or fixture dependencies in the
current lockfile. Their package-provided license files remain authoritative.

| Package | Version | Used through | Declared license | Official source |
| --- | --- | --- | --- | --- |
| `pdfminer-six` | `20260107` | `pdfplumber` | MIT | [project](https://github.com/pdfminer/pdfminer.six) |
| `charset-normalizer` | `3.5.1` | `pdfminer-six`, `reportlab` | MIT | [project license](https://github.com/jawah/charset_normalizer/blob/master/LICENSE) |
| `cffi` | `2.1.1` | `cryptography` | MIT-0 (MIT No Attribution) | [project license](https://github.com/python-cffi/cffi/blob/main/LICENSE) |
| `pycparser` | `3.0` | `cffi` | BSD-3-Clause | [project license](https://github.com/eliben/pycparser/blob/main/LICENSE) |

`pypdfium2` wheels carry PDFium and other dependency license notices in the
package distribution; those notices must be retained if that wheel is
redistributed. ReportLab and Pillow distributions likewise carry their own
package or bundled-asset notices. The fixture factory uses ReportLab's built-in
Helvetica font and creates only from-scratch synthetic labels.

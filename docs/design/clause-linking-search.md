# Clause linking and search design

- 상태: v0.1 대화 설계 승인 완료, 문서 검토 대기
- 적용 단계: Phase 3
- 선행 조건: verified PolicyContract and Rider ledger

## Scope

약관 판본을 조항 단위로 구조화하고, 계약 시점에 맞는 Clause와 실행 가능한 CoverageRule을 실제 가입 Rider에 연결한다. 검색은 조사 도구이며 검색 hit만으로 가입이나 지급 조건을 확정하지 않는다.

## Terms structure

`TermsEdition`은 product/contract applicability, effective period, DocumentVersion과 content hash를 가진다. `Clause`는 parent-child hierarchy, type, label, normalized title/text, 1-based page range와 optional bbox Evidence를 가진다.

지원 type:

- chapter, section, article, paragraph, item
- special terms
- definition
- appendix and table

목차의 표시 page와 PDF physical page를 분리하고 Evidence는 항상 PDF 1-based physical page를 사용한다.

## Search

v0.1은 PostgreSQL만 사용한다.

- Unicode NFC, whitespace와 punctuation normalization
- `simple` text-search configuration의 versioned normalized tokens
- title/Rider name에 `pg_trgm` similarity 보조
- curated synonym table with version and audit
- TermsEdition, effective date, insurer/product, Rider scope filter
- exact phrase and Evidence page projection

검색어와 result text는 일반 log에 남기지 않는다. vector embedding과 별도 search service는 없다.

## Rider-Clause linking

AI structurer는 Rider name, Clause title/text, terms scope를 이용해 link candidate를 만든다. verifier는 후보가 주어진 Evidence와 applicability에 맞는지만 확인한다. deterministic validator는 다음을 검사한다.

- Rider가 policy Evidence로 verified인지
- TermsEdition이 contract date에 적용되는지
- Clause가 해당 DocumentVersion에 존재하는지
- common/special terms scope가 충돌하지 않는지
- page/bbox Evidence가 extraction 범위에 있는지

link 상태는 `AI_VERIFIED`, `NEEDS_REVIEW`, `USER_CONFIRMED`를 사용한다.

## Executable rule publication

CoverageRule candidate는 `docs/design/ai-document-analysis.md`의 allowlist DSL을 사용한다. Rule은 정확한 Clause Evidence, schema/generator/verifier version과 required/optional 성격을 가진다.

지원하지 않는 cross-reference, 손실된 table, 상충 definition은 informational candidate로 남긴다. engine은 이를 실행하지 않고 dependent result를 `UNKNOWN`으로 만든다.

## API boundary

- `GET /api/v1/terms-editions`
- `GET /api/v1/terms-editions/{id}/clauses`
- `GET /api/v1/clauses/search`
- `GET /api/v1/riders/{id}/clause-links`
- `POST /api/v1/rider-clause-links/{id}/confirm|reject`
- `GET /api/v1/coverage-rules/{id}/versions`
- `POST /api/v1/coverage-rules/{id}/publish`

Search response는 Clause label, bounded excerpt, TermsEdition, Evidence와 Rider relevance를 반환한다. raw query와 전체 document text를 response error나 log에 echo하지 않는다.

## Failure behavior

- wrong terms edition candidate는 publish하지 않고 reason code를 남긴다.
- table/appendix Evidence가 끊겼으면 해당 rule만 `NEEDS_REVIEW`다.
- search index rebuild 중에는 기존 version을 제공하고 atomic swap 후 새 version을 사용한다.
- synonym conflict와 normalization version mismatch는 silent fallback하지 않는다.
- 한 Clause parse failure가 다른 searchable Clause를 제거하지 않는다.

## Tests

- hierarchy, appendix/table and physical-page Evidence
- contract date and TermsEdition boundaries
- same Rider name with different definitions
- Unicode/whitespace normalization and Korean synthetic queries
- full-text/trigram ranking baseline with wholly synthetic corpus
- search result outside Rider/date scope exclusion
- AI verifier invented Evidence rejection
- unsupported DSL and cross-reference remain non-executable
- link/rule version publish and stale result handling
- query/document text absence from logs

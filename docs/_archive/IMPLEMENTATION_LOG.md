# atelier — Implementation Log (historical)

> **This is a historical document.** It captures the original v0.1 build plan
> from a specific adopter's perspective and contains user-specific space names
> (`gorae`, `workshop`) that reflect that adoption — not engine defaults.
> For the engine contract, see `docs/ARCHITECTURE.md`. For per-release scope,
> see `CHANGELOG.md`.

이 문서는 atelier v0.1의 최초 설계·실행 계획을 *원형 그대로* 보존합니다. 본문에
등장하는 공간 이름(`gorae`, `workshop`)은 atelier의 contract가 아니라 최초
adopter의 명명일 뿐입니다. Engine 자체는 공간 이름에 대해 agnostic을 지향합니다
(v0.2부터 role 기반 dispatch로 완전 분리될 예정).

---

## A. 합의된 결정 (anchor decisions)

| 항목 | 결정 |
|---|---|
| 프로젝트 이름 | `atelier` |
| 페르소나 | **Librarian + Builder** (2 stewards) |
| Layer 모델 | 3 layers — methodology / content / local |
| `gorae` 위치 | `~/<vault>/` 그대로 (SCHEMA v3→v4 minor bump) |
| `atelier` 거주 | `~/workspaces/atelier/` (private start, public 검토 후속) |
| `atelier-workshop` 거주 | `~/workspaces/atelier-workshop/` (ta-set/private 후신) |
| ta-set 관련 repos | archive (tas, taa, taf, ta-set, ta-set-private) |
| 마이그레이션 | 깨끗한 새 시작 (git history 보존 X) |
| 구현 언어 | Python 3.11+ |
| DB | SQLite + FTS5 (Phase A 채택) |
| DB 거주 | `~/.atelier/cache/atelier.db` (gitignored, derived) |
| Voice overlay | `~/.atelier/voices/*.md` (out of tree) |
| URI scheme | `[[gorae:wiki/...]]`, `[[workshop:products/...]]` |
| 채널 모델 | atelier-server in-process (S1); MCP·HTTPS는 후속 |
| 단일 writer | Librarian → wiki, Builder → workshop |
| Mobile | 고려하지 않음 — 단 *자리 비워둠* (5개 명목 entry) |

## B. Repo·디렉토리 토폴로지

**GitHub repos**

| Repo | 가시성 | 역할 |
|---|---|---|
| `<user>/atelier` | public | methodology — schema · agents · runtime |
| `<user>/<librarian-space>` | private | content — librarian's territory |
| `<user>/<builder-space>` | private | content — builder's territory |
| (legacy / archived) | private (archive) | prior iteration repos, reference only |

**로컬 트리**

```
~/workspaces/atelier/           # atelier code working copy
~/<vault>/              # gorae content working copy (unchanged)
~/workspaces/atelier-workshop/  # workshop content working copy (new)
~/.atelier/                     # per-machine config + derived cache
  ├── config.yaml
  ├── cache/atelier.db
  ├── voices/{librarian,builder}.md
  ├── pii_patterns.txt
  └── secrets/.env
```

## C. atelier 코드 트리 (요약)

```
~/workspaces/atelier/
├── README.md  LICENSE  CLAUDE.md  pyproject.toml  .gitignore
├── docs/                    PLAN, ARCHITECTURE, SCHEMA_V4, ADOPTING
├── schema/
│   ├── data/                base.yaml, librarian.overlay, builder.overlay,
│   │                        linking.yaml, lint.yaml
│   └── db/sql/              0001_initial.sql, ...
├── agents/                  librarian.md, builder.md (culture-neutral)
├── runtime/
│   ├── index/               crawl, parse, linker, entities, writeback, reindex
│   ├── search/              fts, graph, render
│   ├── lint/                L1, L3, L5, L6
│   ├── doctor/              D1–D6, remediate
│   ├── sync/adapters/       github, r2, local_fs
│   ├── promote/             propose, apply
│   ├── service/             api, auth, claims, capture  ← server-shaped
│   └── util/                config, fs, logging
├── config/                  config.schema.yaml, example.config.yaml
├── scripts/                 setup, atelier, new-product, migrate-from-ta-set,
│                            git-hooks/pre-commit
└── tests/                   test_schema, test_index, test_search, test_lint
```

## D. Schema (v4) — 5개 YAML

1. `schema/data/base.yaml` — 공통 frontmatter
2. `schema/data/librarian.overlay.yaml` — gorae raw + wiki
3. `schema/data/builder.overlay.yaml` — workshop
4. `schema/data/linking.yaml` — URI scheme, backward compat
5. `schema/data/lint.yaml` — L1–L7 룰의 데이터화

세부 필드는 Phase 1에서 yaml로 작성. gorae v3 → v4는 *non-breaking* (필드 의미
동일, 권위만 atelier로 이전).

## E. DB (Phase A — SQLite)

테이블 6개 + FTS virtual + 2 view:

- `pages` (slug, type, frontmatter JSON, mtime, content_hash, generated cols)
- `chunks` (page_id, position, text, heading_path)
- `chunks_fts` (FTS5 virtual, unicode61 tokenizer)
- `links` (from_page, to_target, to_page_id, link_type)
- `entities` (canonical_slug, aliases JSON, first_mention, confidence)
- `meta` (key/value: schema_version, atelier_db_version, …)
- view `backlinks_count`, view `broken_links`

세부는 `schema/db/sql/0001_initial.sql`.

## F. CLI 표면 (Phase A)

```
atelier setup
atelier reindex [--space S] [--incremental | --full]
atelier search "<query>" [--space S] [--mode keyword|graph] [--explain]
atelier links <slug> [--inbound | --outbound | --both]
atelier list --type T [--space S]
atelier lint [--rule L1,...] [--fix]
atelier doctor [--remediate --max-usd N]
atelier sync pull|push|status [--space S]
atelier promote propose|apply
atelier capture --text "..." [--source SRC]   # mobile-ready; local-callable v0.1
atelier new-product <name>
atelier migrate-from-ta-set
```

모든 CLI는 `runtime/service/api.py` 함수의 thin wrapper. server-shape 유지.

## G. Phase plan

| Phase | 산출 | acceptance gate |
|---|---|---|
| 0 | foundation: 디렉토리, README/LICENSE/CLAUDE/pyproject/.gitignore, PLAN 자체, pre-commit PII hook, `~/.atelier/` skeleton | git init + initial commit |
| 1 | schema v4 (5 yaml + 0001_initial.sql), gorae SCHEMA v4 minor bump, docs/SCHEMA_V4 | 모든 yaml validate, gorae main에 push |
| 2 | agents/{librarian,builder}.md (culture-neutral), voices/ 사적 overlay, docs/ARCHITECTURE | 페르소나 contract 정의 |
| 3 | runtime/index/*, runtime/util/*, CLI reindex | `atelier reindex --space gorae --full` 성공 |
| 4 | runtime/search/*, runtime/lint/L1·L3·L5·L6, writeback | lint 결과가 기존 gorae lint와 일치 또는 향상; **속도 100× 측정** |
| 5 | runtime/doctor/D1–D6, runtime/sync/adapters/{github,r2,local_fs} | doctor가 drift 감지·복구 |
| 6 | atelier-workshop repo 생성, migrate-from-ta-set, ta-set 5 repos archive | workshop reindex + lint 통과 |
| 7 | runtime/service/{api,auth,claims,capture} — server-shape refactor | 모든 CLI가 service.api 경유 |
| 8 | runtime/promote/{propose,apply}, PROMOTION_LOG | 첫 builder→wiki promotion 1건 |
| 9 | **operational soak (~7 days, passive)** | 일주일 운영, 분기 케이스 docs/OPS_NOTES.md에 누적 |
| 10 | tests/, schema·lint 룰 갱신, docs/ADOPTING.md | pytest 통과, v0.1.0 태깅 |

총 *코드 ~40h + 운영 검증 7d*, 경과 ~2주.

## H. Mobile-ready scaffolding (전 phase 분산)

| 자리 | 처리 |
|---|---|
| base.yaml `source`, `collected_at`, `inbox_status` 필드 | Phase 1 정의 |
| `~/<vault>/raw/personal/inbox/` 디렉토리 | Phase 9 운영 시 생성 |
| `runtime/service/capture.py` | Phase 7 — CLI-callable, MCP-compatible 시그니처 |
| `runtime/service/claims.py`의 `mobile-claim` enum | Phase 7 정의, 검증은 placeholder |
| `config/example.config.yaml`의 `channels.mobile` 주석 | Phase 0 (이미 작성됨) |

## I. Out of scope for v0.1

| 항목 | 이유 | 미래 자리 |
|---|---|---|
| MCP stdio/HTTPS 노출 | service-shape 안정 후 | v0.2 |
| Mobile 채널 실제 활성 | 명시 미고려 | v0.3 |
| Hybrid search (vector + BM25 + RRF) | Phase A로 충분 검증 | v0.3 |
| sqlite-vec / embeddings | 동일 | v0.3 |
| Dream cycle (cron 자동) | 운영 안정 후 | v0.2 |
| L2 hallucination 자동 lint | LLM 의존 | v0.2 |
| Trigram tokenizer (한국어 fuzzy) | unicode61 일단 | v0.2 |
| atelier public 전환 | 안정성 평가 후 | v0.3+ |
| Multi-user federation | 명시 미고려 | v1.x |
| Trust boundary 실제 enforce | 단일 client 동안 placeholder | v0.2 |

## J. Risks (요약)

- ta-set 마이그레이션 데이터 손실 → dry-run 의무
- gorae frontmatter write-back 충돌 → source_count 한정, doctor가 drift 감지
- FTS5 한국어 부정확 → unicode61 + LIKE fallback, Phase B에 trigram
- Phase 9에서 어휘 결함 발견 → v0.1.0 release 보류, ARCHITECTURE 갱신
- atelier에 PII 누설 → Phase 0의 pre-commit PII guard로 차단

## K. 차용한 gbrain 패턴 (즉시)

- **RESOLVER + directory README** — librarian 진입 라우터
- **Page two-layer (compiled truth + timeline)** — wiki 페이지 구조
- **Provenance source labels** — L2 환각 강화
- **Zero-LLM auto-linking** — wikilink → links 테이블 (LLM 0회)
- **Schema versioning + migration files** — 0001_initial.sql 순차

차용 거부: VC CRM page types, 4 DB primitives, 26-skill pool, external
enrichment pipeline.

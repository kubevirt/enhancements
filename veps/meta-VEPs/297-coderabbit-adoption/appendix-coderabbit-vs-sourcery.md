# Appendix: CodeRabbit vs Sourcery — Tool Comparison

## 1. Go Language Support

This is the most decisive factor. KubeVirt and kubevirt/kubevirt are written almost entirely in Go.

- **Sourcery** was built as a Python refactoring engine. Go is among its 30+ "other" languages handled entirely by the LLM layer — no specialized rules, no Go-idiomatic analysis, no concurrency modeling (goroutines, channels, select blocks).
- **CodeRabbit** has first-class Go support: it integrates `golangci-lint` (which bundles `staticcheck` and many other Go-specific analyzers) as part of its 40+ tool suite, and its AST analysis understands Go's type system and package structure.

## 2. Cross-File / Whole-Repo Context

- **Sourcery** reviews the changed files in isolation. It cannot reason about how a type change in one package breaks an interface in another, or how a flag toggle propagates across the codebase. This is a documented, fundamental limitation.
- **CodeRabbit** builds a code graph from AST analysis tracking definitions, references, and call graphs across the entire repository. It uses this to flag cross-package implications of a change — exactly the kind of analysis that matters in a large project like kubevirt/kubevirt.

## 3. Integrated Linter/SAST Toolchain

- **Sourcery** performs AI-driven review but does not orchestrate third-party linters as part of its review pipeline.
- **CodeRabbit** runs 40+ linters and SAST tools in sandboxed environments as part of every review, then feeds their findings into the LLM prompt to produce filtered, contextual feedback. False positives from raw linter output are suppressed before surfacing to the developer.

## 4. Documentation and Knowledge-Driven Review

A reviewer that understands only the code diff is blind to project intent. Both tools allow some form of custom instructions, but the depth differs significantly.

- **Sourcery** accepts custom rules via `.sourcery.yaml` scoped to file-path globs. Rules must be short, concise sentences. While Sourcery lists Jira under its integrations, there is no documented mechanism for it to ingest architectural documents, design rationale, best-practice guides, or policy documents into the review context — the reviewer has no awareness of context that lives outside the changed files.
- **CodeRabbit** provides a layered knowledge base:
  - **Auto-detected guidelines**: reads `CLAUDE.md`, `.cursorrules`, `copilot-instructions.md`, and similar AI agent config files automatically — no manual import.
  - **Plain-English path instructions**: arbitrary prose instructions per file-path glob in `.coderabbit.yaml`, with no length constraint, allowing full policy descriptions, design rationale, and best-practice narratives.
  - **Issue tracker integration**: connects to GitHub Issues, Jira, and Linear so reviews are informed by the linked ticket's acceptance criteria and design notes.
  - **Past PR learnings**: accumulates team feedback from dismissed comments and accepted suggestions to refine future reviews.
  - **Real-time web queries**: fetches current release notes or security advisories when the underlying LLM's training data may be stale.

For a project like KubeVirt this means architecture documents, API design policies, contribution guidelines, and security policies can all be surfaced to the reviewer — making it possible to catch not just code defects but design-level divergence from documented intent.

## 5. Benchmark Performance

In Martian's independent Code Review Bench (the first public benchmark using real developer behavior), CodeRabbit ranked first on both recall and F1 score (51.2%). CodeRabbit's own reporting characterizes its recall lead as ~15% above the next-closest tool. Sourcery did not appear in the benchmark rankings.

## 6. Adoption Evidence

CodeRabbit runs on 2M+ repositories and 9,000+ organizations. It is the most-installed app in the AI category on GitHub Marketplace. Sourcery is a solid tool but has a significantly smaller footprint and is primarily chosen by Python teams.

---

## Summary Table

| Criterion | CodeRabbit | Sourcery |
|---|---|---|
| Go language depth | First-class + Go-specific linters | LLM-only, no specialization |
| Cross-file analysis | Code graph (AST-based) | File-by-file only |
| Linter/SAST integration | 40+ tools, sandboxed, auto-filtered | None built-in |
| Documentation/policy-driven review | Layered KB: guidelines, issues, past PRs, web | Custom rules only (no doc ingestion) |
| Benchmark recall | Highest in independent benchmarks | Not benchmarked comparably |
| Concurrency modeling (Go) | AST + LLM reasoning | No goroutine/channel awareness |
| Noise filtering | Built-in, LLM filters linter output | Manual tuning required |

---

## Sources

- [CodeRabbit AI Code Reviews](https://www.coderabbit.ai/)
- [CodeRabbit Documentation — Code Review Overview](https://docs.coderabbit.ai/guides/code-review-overview)
- [CodeRabbit tops Martian code review benchmark](https://www.coderabbit.ai/blog/coderabbit-tops-martian-code-review-benchmark)
- [Sourcery vs CodeRabbit Comparison](https://www.sourcery.ai/comparisons/coderabbit-alternative)
- [State of AI Code Review Tools in 2025](https://www.devtoolsacademy.com/blog/state-of-ai-code-review-tools-2025/)
- [CodeRabbit Supported Linters](https://www.coderabbit.ai/supported-linters)
- [CodeRabbit commits $1 million to open source](https://www.coderabbit.ai/blog/coderabbit-commits-1-million-to-open-source)
- [AI Native Universal Linter (AST + LLM)](https://www.coderabbit.ai/blog/ai-native-universal-linter-ast-grep-llm)
- [CodeRabbit Knowledge Base](https://docs.coderabbit.ai/knowledge-base)
- [CodeRabbit Context Engineering](https://www.coderabbit.ai/blog/context-engineering-ai-code-reviews)
- [Sourcery Code Review Configuration](https://docs.sourcery.ai/Code-Review/Configuration/)
- [Sourcery Code Review Overview](https://docs.sourcery.ai/Code-Review/Overview/)

# Upstream attribution and scope

## Upstream

- Project: Comet（彗记）
- Repository: `https://github.com/lm041520/Comet`
- Base revision used for extraction: `2ae1c07`

## Why this file exists

Zhiyi AI is a focused derivative prepared for learning, verification and portfolio review. The original system architecture and substantial parts of the initial implementation came from the upstream project. Renaming the product or starting a new Git history does not make that upstream work independently authored.

## Focused changes in this repository

- Limited the product surface to document RAG, long-term memory, Agent tool calling, model configuration and evaluation.
- Removed the original logo and Comet product branding from the active application.
- Removed UI and public API exposure for music, emotion, multi-agent group chat, deep research, scheduled tasks, notifications, sharing and other unrelated features.
- Added and retained local fixes around DeepSeek-compatible structured output, memory retrieval reliability, Neo4j queries and regression tests.
- Replaced the large product dashboard and chat UI with a smaller interface that exposes project evidence directly.

## Authorship language

Recommended description:

> I studied and adapted an existing open-source-style project, extracted the RAG, long-term memory and Agent paths into a focused application, fixed integration and retrieval issues, and verified the result with tests and offline evaluation.

Do not describe the whole codebase or the upstream architecture as independently written from scratch.

## License status

No explicit upstream LICENSE file was found during extraction. This repository records the source and modification scope but does not relicense the upstream code. Users should verify the upstream license status before using or redistributing affected code.

# Project map

| Box | Purpose | Dependencies | Contract and schemas |
|---|---|---|---|
| `automation/pipeline` | Research and write durable editions through model APIs. | Backend HTTP, websearch, source images | [Contract](../automation/pipeline/CONTRACT.md), [config](../automation/pipeline/schema/pipeline-config.schema.json), [result](../automation/pipeline/schema/batch-result.schema.json) |
| `automation/pipeline/source_images` | Find source photographs and upload selected media. | Public source pages, backend media API | [Contract](../automation/pipeline/source_images/CONTRACT.md), [input/output](../automation/pipeline/source_images/schema.json) |
| `automation/executor` | Queue scheduled editions. | Pipeline, backend schedules | [Contract](../automation/executor/CONTRACT.md) |
| `deploy` | Build and upload the public Cloudflare snapshot. | Backend database and media, sibling generator, Cloudflare | [Cache policy](../deploy/CACHING.md), [entry point](../deploy/deploy-cdn.sh) |
| `cli` | Expose operator commands and the guided editorial workflow. | Backend HTTP, pipeline, executor | [Operator surface](../cli/SKILL.md) |
| `newsroom` | Normalize content, topics, and embeds. | Backend HTTP | [Backend contract](../../censurado-web-backend/contracts/CONTRACT.md) |
| Stack | Run the backend and newsroom containers. | Sibling backend and generator | [Map](../AGENTS.md), [Compose](../docker-compose.yml) |

The backend owns content. The generator owns presentation. The executor owns scheduled job coordination.

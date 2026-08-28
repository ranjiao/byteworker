# Digest isolated worker prompt

Execute only the assigned role and shard from the supplied private artifact paths. Resolve the exact role closure
from `references/workflow-routes.json`; do not load `SKILL.md`, unrelated references, another shard, source URL,
KB path, or parent conversation. Write one `byteworker-digest-parallel-result/v1`, preserve every assigned item and
input hash, never mutate the KB, never ask the user, and never spawn another worker. Return only result path,
shard id, record count, or a stable error code.

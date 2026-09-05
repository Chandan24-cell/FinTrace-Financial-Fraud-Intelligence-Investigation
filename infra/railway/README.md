# Railway — Neo4j 5 Community + GDS

PRD §11.2 hosting target.

## Provision

1. `railway login`
2. `railway init` in the repo root, pick *Empty Project*.
3. Add a new Docker service from image `neo4j:5.20-community`.
4. Set environment variables on the service:

   | Key | Value |
   |---|---|
   | `NEO4J_AUTH` | `neo4j/<strong-random-password>` |
   | `NEO4J_PLUGINS` | `["graph-data-science"]` |
   | `NEO4J_dbms_security_procedures_unrestricted` | `gds.*,apoc.*` |
   | `NEO4J_dbms_security_procedures_allowlist` | `gds.*,apoc.*` |
   | `NEO4J_server_memory_heap_max__size` | `2G` |
   | `NEO4J_server_memory_pagecache_size` | `1G` |

5. Attach a 5 GB persistent volume mounted at `/data`.
6. Expose Bolt (7687) publicly (Railway → Networking → Generate Domain → TCP proxy).
7. Copy the public Bolt URI into the backend `.env.local` as `NEO4J_URI`.

## Verify

From Neo4j Browser (https://<railway-domain>:7474), run:

```cypher
RETURN gds.version()
```

If this returns a version string, the GDS plugin is loaded correctly.
This is PRD §10 Day 1 acceptance criterion.

# Local stack (Docker Desktop)

| Service | Stands in for | Port on localhost |
|---|---|---|
| hms, statestored, catalogd, impalad | Cloudera Data Warehouse (Impala) | 21050 HS2, 28000 HS2-HTTP, 25000 web UI |
| bridge | Workbench Application `prime-agent-mcp` | 18080 |
| console | Workbench Application `investigation-console` | 18090 |
| ollama (profile `ollama`) | Cloudera AI Inference | 11434 |
| tunnel (profile `tunnel`) | the Workbench application URL | prints a public URL |

The Impala images are amd64 only. On Apple Silicon they run under emulation;
give Docker Desktop at least 8 GB and expect slow startup.

Common commands (from the repo root):

    make up                       # impala + bridge + console
    make up PROFILES=ollama       # add the local child model
    make tunnel                   # add the public tunnel, print its URL
    make impala-shell             # interactive impala-shell
    make smoke                    # connectivity, Iceberg DDL, optional Prime Agent run
    make down                     # stop; `make down V=1` also deletes the warehouse

To point children at Ollama instead of Mistral, set in `.env`:

    CAII_BASE_URL=http://ollama:11434/v1
    CAII_TOKEN=ollama
    CAII_MODEL=ministral-3:3b
    CHILD_MODEL=cloudera-ai-inference/ministral-3:3b

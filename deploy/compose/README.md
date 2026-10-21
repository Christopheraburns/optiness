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

Loading data (BURNDOWN D4). Meridian generates the data in its own repo; `make data`
stages one complete public run and loads it into the Iceberg tables:

    make schema                                   # once: create the 12 tables
    make data                                     # default ../meridian/data/demo-public
    make data MERIDIAN_PUBLIC_DIR=../meridian/data/demo-50m-public REPLACE=1

The emulated impalad is fragile on writes. Multithreaded (`mt_dop`) or LLVM-codegen'd
partitioned inserts can wedge it: either every thrift server dies with "Interrupted
system call", or a fragment stops with 0% CPU. `data/load.py` therefore writes with
`MT_DOP=0` and `DISABLE_CODEGEN=true` on the local stack, a 180 s time limit, and
retries. If impalad still wedges, `docker restart primeagent-impalad-1` and re-run
`make data`; it resumes where it stopped. Agent reads use the default options.

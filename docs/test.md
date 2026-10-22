flowchart LR
    U([Banker in<br/>Mistral Studio chat])
    S[Mistral Studio agent<br/>'Returns Investigator']

    subgraph CAI[Cloudera AI Workbench, or Docker locally]
        B[["Bridge<br/>(prime-agent-mcp app)"]]
        DB[(Logbook<br/>SQLite)]
        PA[Prime Agent<br/>root on Mistral Large 4]
        C1[Child agent<br/>Mistral Medium]
        C2[Child agent<br/>Mistral Medium]
        C3[Child agent<br/>Mistral Medium]
        CON[Investigation Console<br/>for people to watch]
    end

    subgraph DATA[Bank data platform]
        IMP[(Impala / Iceberg<br/>payment tables)]
        ATL[Atlas catalog]
    end

    U -- asks a question --> S
    S -- "MCP tool calls + secret token<br/>(over the internet)" --> B
    B -- records jobs --> DB
    B -- "starts & supervises<br/>(ACP)" --> PA
    PA -- spawns --> C1 & C2 & C3
    PA & C1 & C2 & C3 -- read-only SQL --> IMP
    PA -- table meanings --> ATL
    CON -. reads run files .-> PA

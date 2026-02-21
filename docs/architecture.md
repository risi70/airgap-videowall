# Architecture

## Component view

```mermaid
flowchart LR
  subgraph SZ["Source Zone"]
    SA["Source Agent (VDI / HDMI encoder)\nTLS+mTLS\nGStreamer"]
    ENC["HDMI-to-IP Encoder\nRTSP/SRT"]
  end

  subgraph MC["Media Core Zone (Kubernetes)"]
    subgraph VWCTL["vw-control namespace"]
      MGMT["vw-mgmt-api\nFastAPI\nOIDC JWT validation\nPolicy PEP"]
      POL["vw-policy\nABAC/RBAC engine"]
      AUD["vw-audit\nappend-only hash chain"]
      HLTH["vw-health\nhealth aggregation"]
      KC["Keycloak\nOIDC"]
      VA["Vault PKI\nCA + issuance"]
      PG["PostgreSQL 15"]
    end

    subgraph VWM["vw-media namespace"]
      SFU["Janus SFU\nWebRTC"]
      GW["GStreamer Gateway\nRTSP/RTP/SRT ingest"]
      COMP["Compositor\nMosaic render"]
    end

    subgraph OBS["vw-obs namespace"]
      PROM["Prometheus"]
      GRAF["Grafana"]
      LOKI["Loki"]
      PT["Promtail"]
    end
  end

  subgraph DZ["Display Zone"]
    WC["Wall Controller\nheartbeat + layout"]
    TP["Tile Player\nmpv/kiosk"]
    BS["Big Screen Player\n4K"]
  end

  SA -->|mTLS + REST| MGMT
  ENC -->|RTSP/SRT| GW
  SA -->|RTP/SRT| GW
  GW -->|WebRTC/RTP| SFU
  SFU -->|Media| TP
  SFU -->|Media| BS
  MGMT <--> POL
  MGMT --> AUD
  MGMT <--> PG
  MGMT -->|OIDC| KC
  MGMT -->|cert issue| VA

  MGMT -->|/metrics| PROM
  SFU -->|/metrics| PROM
  GW -->|/metrics| PROM
  COMP -->|/metrics| PROM
  PT -->|logs| LOKI
  GRAF -->|queries| PROM
  GRAF -->|logs| LOKI
```

## Sequences

### a) Operator assigns source to wall
```mermaid
sequenceDiagram
  actor Op as Operator
  participant UI as Mgmt UI
  participant KC as Keycloak
  participant MG as vw-mgmt-api
  participant PL as vw-policy
  participant AU as vw-audit
  Op->>UI: Select wall + source
  UI->>KC: OIDC auth
  KC-->>UI: JWT (roles + clearance_tags)
  UI->>MG: POST /walls/{id}/assign (JWT)
  MG->>PL: Evaluate ABAC/RBAC (wall, source, tags)
  PL-->>MG: allow/deny
  alt allow
    MG->>AU: Append audit record + hash
    MG-->>UI: 200 OK (assignment)
  else deny
    MG-->>UI: 403 Forbidden
  end
```

### b) Tile player subscribes
```mermaid
sequenceDiagram
  participant TP as Tile Player
  participant WC as Wall Controller
  participant MG as vw-mgmt-api
  participant PL as vw-policy
  TP->>WC: Request playback details (local)
  WC->>MG: Request subscribe token (mTLS)
  MG->>PL: Evaluate subscription authorization
  PL-->>MG: allow + constraints
  MG-->>WC: Signed subscribe token + URL
  WC-->>TP: URL + token
  TP->>MG: (optional) validate token freshness
  TP->>SFU: Subscribe/play (token)
```

### c) Compositor renders mosaic
```mermaid
sequenceDiagram
  participant COMP as Compositor
  participant MG as vw-mgmt-api
  participant SFU as SFU
  MG-->>COMP: Desired mosaic layout + source streams
  COMP->>SFU: Pull/subscribe inputs
  SFU-->>COMP: Media frames
  COMP-->>SFU: Composite output stream
  SFU-->>Players: Per-tile/big-screen streams
```

## Data flow notes
- Control plane uses **mTLS** between services and agents.
- Operator plane uses **OIDC** (Keycloak) to obtain JWT; mgmt-api validates and enforces RBAC/ABAC.
- Media plane uses WebRTC/SRTP (interactive) or RTP/SRT (lower complexity) depending on endpoint class.

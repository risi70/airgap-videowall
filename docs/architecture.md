# Module 2 Architecture (Media Plane)

## Sequence: source → gateway → SFU → tile player

```mermaid
sequenceDiagram
    participant S as Source (RTSP/SRT/RTP)
    participant GW as vw-gw (GStreamer Gateway)
    participant SFU as vw-sfu-janus (Janus)
    participant TP as Tile Player (Display Zone)

    Note over GW: /probe uses ffprobe (10s timeout)
    GW->>S: Pull input (RTSP/SRT/RTP)
    GW->>GW: Depay/parse/mux
    GW->>SFU: (Optional) publish stream as WebRTC (future integration)
    SFU->>TP: WebRTC signaling (HTTP/WS) + RTP media (20000-20200/UDP)
```

## Sequence: compositor policy check → pull sources → SRT output

```mermaid
sequenceDiagram
    participant OP as Operator (via mgmt-api)
    participant COMP as vw-compositor
    participant POL as vw-policy (vw-control)
    participant SRC as Source
    participant OUT as SRT Sink / Decoder

    OP->>COMP: POST /mosaics (inputs[])
    loop for each input.source_id
      COMP->>POL: POST /evaluate {source_id, action}
      POL-->>COMP: allow/deny
    end
    alt any denied
      COMP-->>OP: 403 denied
    else all allowed
      OP->>COMP: POST /mosaics/{id}/start
      COMP->>SRC: Pull sources (RTSP/SRT)
      COMP->>OUT: Push mosaic as SRT MPEG-TS
    end
```

## Component view (Module 2)

```mermaid
flowchart LR
  subgraph SZ[Source Zone]
    SRC1[RTSP Source]
    SRC2[SRT Encoder]
  end

  subgraph MC[vw-media (Kubernetes)]
    GW[vw-gw :8004]
    COMP[vw-compositor :8005]
    SFU[vw-sfu-janus :8088/:8188\nRTP 20000-20200/UDP]
  end

  subgraph CZ[vw-control]
    POL[vw-policy :8002]
    MGMT[vw-mgmt-api]
  end

  subgraph DZ[vw-display]
    TP[Tile Player / Decoder]
  end

  MGMT -->|HTTP 8004| GW
  MGMT -->|HTTP 8005| COMP
  MGMT -->|HTTP/WS 8088/8188| SFU

  COMP -->|HTTP 8002| POL
  GW -->|RTSP/SRT/RTP| SZ
  COMP -->|RTSP/SRT| SZ
  SFU -->|RTP 20000-20200| TP
  TP -->|HTTP/WS 8088/8188| SFU
```

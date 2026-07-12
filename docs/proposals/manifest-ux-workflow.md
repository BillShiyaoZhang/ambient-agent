# Manifest UX Workflow

## Purpose

The Manifest remains a machine-readable contract, but users should not need to
read or edit raw JSON. Ambient Agent can translate validated declarations into
a concise explanation when an App is modified.

These diagrams clarify three different questions:

1. What does the user experience during a modification?
2. Which responsibilities belong to the user, Agent, and platform?
3. What future lifecycle boundary keeps the current App available on failure?

Only the contract, validation, one-time migration, and AppManager integration
belong to Phase 1. The review, activation, and recovery flows are product and
architecture boundaries for later work, not implementation commitments.

## 1. Future user-facing modification workflow

This future path, which is not implemented in Phase 1, separates machine
validation from user-facing review while avoiding unnecessary confirmation
for low-risk updates.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontFamily": "Inter, ui-sans-serif, system-ui",
    "primaryTextColor": "#172033",
    "lineColor": "#758399"
  },
  "flowchart": {
    "curve": "basis",
    "nodeSpacing": 30,
    "rankSpacing": 44
  }
}}%%

flowchart LR
    REQUEST(["Request change<br/>Add a weekly view"]):::warm
    BUILD["Build candidate<br/>Source + Manifest"]:::cool
    VALIDATE{"Contract<br/>valid?"}:::coolStrong
    REVIEW{"Review<br/>needed?"}:::hero
    PREVIEW["Explain change<br/>Purpose and impact"]:::warm
    CONSENT{"Apply<br/>update?"}:::warmStrong
    UPDATED(["Updated App<br/>New version active"]):::coolFinal
    PRESERVED(["Current App<br/>Remains available"]):::safe

    REQUEST --> BUILD --> VALIDATE
    VALIDATE -->|Yes| REVIEW
    VALIDATE -->|No| PRESERVED
    REVIEW -->|No| UPDATED
    REVIEW -->|Yes| PREVIEW --> CONSENT
    CONSENT -->|Apply| UPDATED
    CONSENT -->|Cancel| PRESERVED

    classDef warm fill:#FFF2EF,stroke:#E8C7C0,color:#6B3931,stroke-width:1px;
    classDef warmStrong fill:#EFD2CB,stroke:#C77D6E,color:#592D25,stroke-width:1.5px;
    classDef cool fill:#F1F6FA,stroke:#C8D7E2,color:#29465E,stroke-width:1px;
    classDef coolStrong fill:#D6E5F0,stroke:#789DB9,color:#183B57,stroke-width:1.5px;
    classDef coolFinal fill:#C7DCEB,stroke:#5E88A8,color:#183B57,stroke-width:1.5px;
    classDef safe fill:#EDF2F5,stroke:#94A8B6,color:#2C4658,stroke-width:1.4px;
    classDef hero fill:#142131,stroke:#142131,color:#FFFFFF,stroke-width:2.2px;
```

The Manifest can support the explanation by providing validated identity,
purpose, version, intent hints, and central schema references. It does not
prove that the code has no other behavioral or data-access changes, and schema
references do not grant permissions or authorize graph mutations.

`Review needed?` represents a future UX policy. Meaningful purpose or behavior
changes may warrant a preview, while a low-risk visual correction should not
necessarily interrupt the user.

## 2. Future responsibility boundary

This future platform-gate view keeps ownership visible without three heavy
containers. Phase 1 establishes only Manifest contract validation; activation
and preservation remain later platform responsibilities.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontFamily": "Inter, ui-sans-serif, system-ui",
    "primaryTextColor": "#172033",
    "lineColor": "#758399"
  },
  "flowchart": {
    "curve": "basis",
    "nodeSpacing": 28,
    "rankSpacing": 42
  }
}}%%

flowchart LR
    U1(["USER<br/>Describe outcome"]):::warm
    A1["AGENT<br/>Interpret request"]:::cool
    A2["AGENT<br/>Build candidate"]:::cool
    P1["PLATFORM<br/>Validate contract"]:::coolStrong
    P2{"Candidate<br/>valid?"}:::hero
    A3["AGENT<br/>Explain impact"]:::cool
    U2["USER<br/>Review impact"]:::warm
    U3{"USER<br/>Accept change?"}:::warmStrong
    P3(["PLATFORM<br/>Activate candidate"]):::coolStrong
    P4(["PLATFORM<br/>Preserve active App"]):::safe

    U1 --> A1 --> A2 --> P1 --> P2
    P2 -->|Review needed| A3 --> U2 --> U3
    P2 -->|Valid, no review| P3
    P2 -->|Invalid| P4
    U3 -->|Accept| P3
    U3 -->|Cancel| P4

    classDef warm fill:#FFF2EF,stroke:#E8C7C0,color:#6B3931,stroke-width:1px;
    classDef warmStrong fill:#EFD2CB,stroke:#C77D6E,color:#592D25,stroke-width:1.5px;
    classDef cool fill:#F1F6FA,stroke:#C8D7E2,color:#29465E,stroke-width:1px;
    classDef coolStrong fill:#D6E5F0,stroke:#789DB9,color:#183B57,stroke-width:1.5px;
    classDef safe fill:#EDF2F5,stroke:#94A8B6,color:#2C4658,stroke-width:1.4px;
    classDef hero fill:#142131,stroke:#142131,color:#FFFFFF,stroke-width:2.2px;
```

The responsibilities are:

- **User:** describe the desired outcome, understand meaningful impact, and
  accept or cancel when review is required.
- **Agent:** interpret the request, prepare a candidate, and write a readable
  explanation.
- **Platform (future lifecycle):** validate the contract, control activation,
  and preserve the current App when the candidate cannot proceed.

## 3. Future candidate lifecycle and recovery boundary

A state diagram expresses desired lifecycle boundaries more accurately than
another workflow. It separates stable states from validation, consent,
activation, and recovery transitions.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontFamily": "Inter, ui-sans-serif, system-ui",
    "primaryTextColor": "#172033",
    "lineColor": "#758399"
  }
}}%%

stateDiagram-v2
    direction LR

    state "Active App" as Active
    state "Candidate prepared" as Candidate
    state "Contract validation" as Validate
    state "Awaiting review" as Review
    state "Activating candidate" as Activating
    state "New version active" as Updated
    state "Active App preserved" as Preserved

    [*] --> Active
    Active --> Candidate: modification request
    Candidate --> Validate
    Validate --> Review: valid / review needed
    Validate --> Activating: valid / no review
    Validate --> Preserved: invalid
    Review --> Activating: user accepts
    Review --> Preserved: user cancels
    Activating --> Updated: activation succeeds
    Activating --> Preserved: activation fails
    Updated --> Active: becomes current
    Preserved --> Active: retain or restore

    classDef stable fill:#D6E5F0,stroke:#789DB9,color:#183B57,stroke-width:1.5px;
    classDef working fill:#F1F6FA,stroke:#C8D7E2,color:#29465E,stroke-width:1px;
    classDef review fill:#EFD2CB,stroke:#C77D6E,color:#592D25,stroke-width:1.5px;
    classDef safe fill:#EDF2F5,stroke:#94A8B6,color:#2C4658,stroke-width:1.4px;

    class Active,Updated stable
    class Candidate,Validate,Activating working
    class Review review
    class Preserved safe
```

Candidate staging, atomic activation, previous-version recovery, and
activation-failure handling are beyond Phase 1. The diagram records a safety
boundary for later design, not a guarantee implemented by the Manifest PR: an
unsuccessful candidate should not destroy the currently usable App.

# ORNL report: QRMI vs QDMI analysis

Source: [openQSE/development-analysis: `qrmi-qdmi/qrmi-qdmi-analysis.md`](https://github.com/openQSE/development-analysis/blob/main/qrmi-qdmi/qrmi-qdmi-analysis.md).

- **Context:** ORNL presented it at openQSE Day and discussed it at the QRMI open call on 25/08/2026.
- **QRMI's response:** Ohtani said 0.24.4 (8 Sep) "addressed all issues raised in the ORNL's analysis report that can be resolved at this time", and 0.26.0 fixed further IQM and IBM QS bugs reported by ORNL.
- **Versions:** qrmi 0.24.4 (originally 0.17.2) against iqm-qdmi 1.4.0, mqt-core 3.9.2 and iqm-client 34.0.1, tested live on ORNL's 20-qubit IQM system. Both paths returned the same topology and counts.
- **Caveat:** written from WebFetch summaries of a long document, so check exact figures against the source before quoting them.

## Headline

**QRMI and QDMI aren't alternatives. They sit at different layers.**

- **QRMI** faces the resource manager:
  - one `QuantumResource` trait with 12 async methods;
  - vendors built in (`ibm.rs`, `iqm.rs`, `alice_bob.rs`…), with a closed `ResourceType` enum;
  - Rust core, plus C (cbindgen) and Python bindings.
- **QDMI** faces the device and is extended through properties.
- **Flattening them is costly:** treating them as interchangeable providers (as the QFw shim does) loses this distinction.

**The report's central claim is that provider-side change is the main risk.** QRMI passes through thick, raw provider JSON with no transition policy, and its generated clients inherit the provider's release cadence, so vendor API changes break callers.

## Findings by area

| Area | QRMI finding | Status |
|---|---|---|
| Discovery and capability | Configuration- and scheduler-driven: a resource map, or env vars such as `QRMI_JOB_QPU_RESOURCES` and `SLURM_JOB_QPU_RESOURCES`. Capability is implied by the resource type, and nothing reports supported formats or operations. | Open |
| Admission (`acquire`/`release`) | IQM `acquire()` returns a generated UUID without contacting the provider, and `release()` is `Ok(())`. Since 0.24.4, the defaults log a warning but still return **success**, so callers can't tell a real reservation from a no-op. IQM's `release` doc comment describes session behaviour it doesn't have. | Open |
| Admission policy | Only deployment knobs (resource maps, `*_QRMI_JOB_TIMEOUT_SECONDS`). No quota, credit, rate-limit or QoS; site policy lives in Slurm. | By design; wants better *inputs* (load, queue depth, expected wait) |
| Device scheduler control | `task_start` takes no priority, deadline or queue selection; only IQM has `use_timeslot`. Ordering is decided twice (site scheduler, then provider queue) with no channel between the two. | Open |
| Runtime submission | The caller builds the vendor-native payload (e.g. an IQM `RunRequest`), which couples every caller to the vendor SDK. An iqm-client change broke QFw (QFw#31). | Open |
| Results and lifecycle | `task_result` returns raw vendor JSON. There is no wait call, so callers poll. Provider timing isn't returned. | Open |
| Introspection (`target()`) | One raw JSON document built from 3 IQM REST calls. The optional `static_quantum_architecture` field (0.22.0) is a **list** and `null` on a 404, which broke a consumer (`'list' object has no attribute 'get'`). `target()` doesn't need `acquire()`. | Hazard noted |
| Calibration | Full IQM calibration and quality payloads pass through. This is QRMI's strength: the only path to full calibration analytics. | Strength |
| Telemetry | No queue-state call (`queue_position` is null). The IQM job timeline arrives through `task_logs()`, but as a fixed-width string that schedulers must parse. The native client's queue and execution timings are discarded. The 1.26 MB calibration document is fetched on every `target()`. | Open |
| Performance | Fastest cold start: ~1.3–1.45 s against ~3.4 s for QDMI (later ~1.5 s at iqm-qdmi 1.4.0) and ~2.8 s native, thanks to one pooled connection. A cache makes repeat calls ~40× cheaper, but there's no stated freshness or bypass policy. | Good; caching policy unspecified |
| Authentication | Credentials are read once at construction from env vars (e.g. `{backend}_QRMI_IQM_ISA_TOKEN`), injected by SPANK. This suits a scheduler-injected model, while QDMI suits an in-process one. | Wants a defined injection contract (env or parameters) |
| Authorization | One credential, one privilege level. The real boundary is the Slurm prologue running as root. Neither interface separates execution rights from operator rights. | Only matters if an operator API is added |
| Data normalization | Fragile to vendor changes. IQM's health-status response changed shape, and the generated client's required fields broke `is_accessible()`. Successive releases fixed one deployment and broke the other (0.23.1, 0.24.0, 0.24.3, 0.24.4). **No release works on both ORNL q20 and Resonance.** This contrasts with the 0.23.0 resource-type rename, which had a proper transition window. | Open, reported upstream |
| Program format and placement | The format is implied by the resource type, with no negotiation. Placement exists only as physical qubit names inside the payload (`"locus": ["QB1"]`). | Open |
| Error model | Through 0.21.0, failed fetches became `null` with `Ok`. Fixed in 0.22.0 (errors propagate, plus a Python logging bridge) and 0.24.0 (typed `QrmiError` with `.kind()` and a Python exception hierarchy). Only 4 kinds are classified; e.g. 403 falls into `Other`. | Mostly closed |
| Extensibility and versioning | Closed enums (`ResourceType` with 7 variants, `Payload` with 4), so adding one is breaking. The monolithic 12-method trait led to no-op implementations. No runtime version or capability discovery; probing means invoking, which is unsafe for acquire, submit and cancel. The payload schema is versioned, but the interface isn't. | Open |

## Recommendations

The report doesn't rank them; this order follows its emphasis.

1. **Transition policy for provider payloads:**
   - accept both old and new shapes during a declared window;
   - treat response fields as optional unless the provider commits to them;
   - version the passthrough data.
2. **Capability advertisement and version discovery:**
   - let callers ask in advance what a resource supports;
   - return a typed "not supported" instead of a silent success. "A call that cannot enforce should fail rather than return a token."
3. **Separate the runtime envelope from the program payload:**
   - use an agreed exchange format (`openqse.exchange.v1`) with a `provider.native` escape hatch;
   - make format and placement first-class, negotiable fields.
4. **Telemetry and provenance:**
   - queue and load state as first-class data;
   - structured timing that separates queue time from execution time;
   - a provenance record for each job.
5. **Feed scheduling decisions:**
   - carry priority and deadline intent down to the provider;
   - let submissions reference site reservations;
   - expose provider queue state.
6. **Specify caching and transport contracts:** freshness, staleness, bypass and connection reuse.
7. **Credential injection contract:** separate getting credentials from the execution API, and support both env-var and parameter delivery.
8. **Authorization split:** needed only if an operator-facing API is added.

## Relevance to the operator and the talk

- **`acquire()` is a no-op for most backends.** This backs up Vanessa's point (see `vanessa-feedback.md`). A claim reaching `Bound` means "acquire returned", not "QPU reserved".
  - Our own Felis backend is the same: the demo workload logs an "acquire dummy" warning. Avoid implying in the talk that a Bound claim reserves the QPU.
- **No queue-state or capability API.** The operator can't yet bind claims on real device readiness, which is the idea Yoonho raised for the paper.
- **Env-var credential injection is exactly the operator's model.** The report wants it formalised as a contract rather than replaced.
- **The typed error work (0.24.0)** is what the operator's `{e:#}` logging now surfaces. It's worth mentioning that QRMI errors used to be swallowed.

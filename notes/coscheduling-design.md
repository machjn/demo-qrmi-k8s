# Co-scheduling quantum and classical resources: design note

Status: draft, for discussion. Nothing here is implemented.

Related: `vanessa-feedback.md` and `ornl-qrmi-report.md`.

## 1. Definition

> **Co-scheduling** means allocating the classical and quantum resources that a job needs so that they're available at the same time. Neither should be held idle waiting for the other, beyond some bound.

Three properties follow:

- **It's a scheduling-time decision.** The decision is made *before* resources are committed. Waiting inside a running workload isn't co-scheduling; that's what happens when there isn't any.
- **It needs a view of both sides.** The scheduler has to know when the quantum resource will be usable, not just that it exists and the credentials work.
- **It works in both directions.** Classical nodes shouldn't idle waiting for the vendor queue. A quantum session or reservation, which may be billed or time out, shouldn't idle waiting for classical capacity either.

In HPC, "co-scheduling" (or gang scheduling) traditionally means starting all of a job's processes at once on resources one scheduler controls. Here the term is stretched across resources owned by **different schedulers**: the cluster's, and the vendor's queue. That's what makes it hard.

## 2. Levels

The term gets used for very different guarantees, so it helps to name them.

| Level | Name | Guarantee | Needs | Example |
|---|---|---|---|---|
| 0 | None | Classical resources are allocated; quantum is waited for at runtime. | Nothing | Plain Job calling QRMI |
| 1 | Availability gating | Don't start classical work if the quantum endpoint is down or the credentials are bad. | Health check / `acquire()` | **The operator today**, SPANK plugin |
| 2 | Reactive (best effort) | Start most of the classical work only once the quantum task is about to run. | Vendor queue visibility, plus a minimal "leader" that submits the task | fluence (Vanessa) |
| 3 | Reservation-based | Reserve a quantum time window, then schedule classical work into it. | Vendor reservation API, or events | IQM reservations, Braket reservations |
| 4 | Joint control | Quantum is a schedulable resource in the same scheduler as classical. | Local or on-prem control of the QPU | Flux resource graph with an on-prem device |

Only levels 3 and 4 are co-scheduling in the strict sense. Level 2 is the best you can do against a vendor queue you can't control.

## 3. Where the operator is today

- **What happens:** the Job is suspended, the claim calls `acquire()`, the claim becomes `Bound`, the Job is unsuspended, and the pods are scheduled. Then the workload submits its task and waits in the vendor queue.
- **What `acquire()` guarantees:** for most backends very little. IBM Direct Access, IQM and Felis return a generated ID. IBM Qiskit Runtime opens a session, but the first job still queues normally. So `Bound` means "endpoint reachable, credentials valid", which is **level 1**.
- **What's already in place:** the claim lifecycle, release guaranteed by a finalizer, and credential hand-off. These still apply at higher levels. What's missing is (a) a readiness signal and (b) a general way to hold workloads back.

## 4. Constraints

1. **There's no readiness signal in QRMI.** No queue position, no ETA, no reservation, no event. The ORNL report and Vanessa both flag this, and Ohtani has floated some shapes for it (§5.1).
2. **You can't join the queue without running something.** Most vendor APIs queue a *task*, not a *request for capacity*. Submitting a task means some of the application must run. Level 2 accepts a small idle cost (one leader pod) to get around this.
3. **Holding classical capacity.** When the quantum side becomes ready, classical resources must actually be available. Otherwise the problem just flips direction. Something has to hold or prioritise classical capacity while the quantum side is pending.
4. **Pods are not only Jobs.** Job `suspend` doesn't generalise to other workload types (Vanessa's critique). Pod scheduling gates do.
5. **Credentials.** At level 2 the leader pod submits with whatever credentials the workload has, so this works under both the admin-owned and the user-owned credential models.

## 5. Design

### 5.1 Separate "acquired" from "ready" on the claim

Add a readiness stage to `QuantumResourceClaim`:

```
Pending ──acquire()──▶ Acquired ──readiness signal──▶ Bound ──▶ Released
                          │
                          └── timeout / vendor rejects ──▶ Failed
```

- **`Bound` means "the quantum resource is usable now"**, or within a configured tolerance. Today it means only that `acquire()` returned.
- **Status carries what's known**, e.g. `status.quantum: {position, eta, reservationWindow}`, so `kubectl get qrc` shows why a claim is waiting.
- **The source of the readiness signal is per resource.** It's configured on the `QuantumResource`, e.g. `spec.readiness.mode`:

| Mode | Level | Meaning |
|---|---|---|
| `Acquire` (default) | 1 | Ready when `acquire()` succeeds. This is today's behaviour. |
| `QueueProbe` | 2 | Ready when the leader's task reaches queue position ≤ N, or its ETA is ≤ T. |
| `Reservation` | 3 | Ready when the vendor confirms the reserved window has started. |

- **Proposed QRMI extension.** This would be raised upstream; it overlaps with Ohtani's ideas and the ORNL recommendations.
  - `acquire()` returns a token *plus a status*.
  - A new call, `acquisition_status(token)`, returns one of:
    - `Pending { position?, eta? }`
    - `Ready { until? }`
    - `Expired`
    - `Unsupported`
  - Backends that can't answer return `Unsupported`, and the operator falls back to `Acquire` mode. This also addresses the ORNL point that no-op calls shouldn't look like success.

### 5.2 Holding workloads: use Kueue admission checks

Rather than extending our own suspend logic, integrate with [Kueue](https://kueue.sigs.k8s.io/), the Kubernetes SIG project for job queueing.

**How Kueue admits a workload:**
1. It reserves quota.
2. It waits for every configured **admission check** to report `Ready`.
3. It admits the workload, which unsuspends a Job or removes the scheduling gates from pods.

**What the operator would do:** act as an admission-check controller.

```
Job submitted (queue label + quantum.qrmi.io/resource annotation)
  └─▶ Kueue creates a Workload and reserves classical quota
        └─▶ AdmissionCheck "qrmi" Pending
              └─▶ operator creates the QuantumResourceClaim, acquire(), waits for readiness
                    └─▶ claim Bound → operator sets the check to Ready
                          └─▶ Kueue admits → pods scheduled
```

**Why Kueue:**
- **The order is right:** classical quota is reserved *before* the quantum wait, which covers constraint 3, at least at the level of quota.
- **More than Jobs:** Kueue already holds back plain Pods, JobSets, MPIJobs, RayJobs, and Deployments and StatefulSets through its pod integration. That covers constraint 4 without us building a webhook.
- **Quotas, fair share, priorities and preemption come for free.** That answers Vanessa's fair-share and quota questions for the classical side.
  - A per-QPU quota could be modelled as a Kueue resource flavor or a custom resource. That needs investigating.
- **Retries:** the admission check API already has `Retry` and `Rejected` states, which map onto timeout and `Failed`.

**Caveats:**
- Quota is not physical capacity. A reserved quota slot can still meet a full node pool. Priority and preemption help.
- Kueue becomes a dependency.
- Verify the API (`AdmissionCheck`, `controllerName`, Workload `status.admissionChecks`) against the Kueue version we'd target.

**Fallback without Kueue:** add pod scheduling gates with a mutating webhook, and have the operator remove the gate when the claim is `Bound`. This is more general than Job suspend, but we'd have to build queueing, quota and fair share ourselves.

### 5.3 Leader pattern for `QueueProbe`

Vendors queue tasks, not capacity requests, so level 2 needs a minimal submitter:

- **The leader:** a single pod (index 0 of a JobSet or indexed Job, or a small dedicated probe pod) is admitted first. Under Kueue this could be a separate small Workload.
- **Reporting:** the leader submits the quantum task and reports its task ID on the claim, e.g. through a status sub-resource or an annotation the operator watches.
- **Polling:** the operator polls the queue position, via QRMI if supported or a vendor-specific probe otherwise.
- **Admission:** when the task is within threshold, the claim becomes `Bound` and the rest of the workload is admitted.

**Trade-off:** one pod idles instead of N. This is the fluence design, and it should be credited and compared in the paper.

### 5.4 Timeouts and failure

- **Quantum ready, classical not admitted within `T1`:** release or let the session expire, and requeue. Avoids paying for an idle reservation.
- **Claim pending longer than `T2`:** mark it `Failed` and set the admission check to `Rejected` or `Retry`. This is the "Failed phase / retry cap" open question.
- **Release is guaranteed in every case** by the existing finalizer.

## 6. Phased plan

1. **Level 1, done:** the current operator.
2. **Readiness plumbing:**
   - the `Acquired` phase;
   - `spec.readiness.mode` with `Acquire` as the default;
   - richer claim status;
   - no behaviour change.
3. **Kueue admission-check controller:** works at level 1 straight away, and gives Kueue's workload types, quotas and fair share.
4. **`QueueProbe` for one backend with queue visibility**, e.g. IBM Runtime or Braket via a probe. This includes the leader pattern.
5. **QRMI upstream:** propose `acquisition_status()` and reservation semantics. Then implement `Reservation` mode for a vendor that supports it.
   - Alice & Bob: consider a reservation API for Felis.

## 7. Open questions

- **Readiness tolerance:** how close to the head of the queue is "ready"? Position ≤ 1, or ETA ≤ the classical start-up time? This probably needs to be configurable per resource.
- **Where QPU quotas live:** per-device, cost-aware quotas could be Kueue flavors, or claim-level limits on the `QuantumResource`.
- **Long-lived workloads:** Deployments and services that need QPUs intermittently don't fit an "acquire before start" model. Should claims be dynamic, requested by the running workload, with the operator mediating?
- **User-owned credentials:** at level 2 the leader needs credentials to submit. Can the claim reference a user-namespace Secret instead of the admin one?
- **Hard dependency or optional:** should Kueue be required, or an optional integration with suspend kept as the default?

## 8. References

- Vanessa's co-scheduling critique and the fluence design: `#slurm-development`, 30 Jun 2026 thread; [fluence](https://github.com/converged-computing/fluence).
- ORNL QRMI/QDMI analysis, sections on admission, device scheduler control and telemetry: [openQSE/development-analysis](https://github.com/openQSE/development-analysis/blob/main/qrmi-qdmi/qrmi-qdmi-analysis.md).
- Ohtani on acquire semantics (UUID plus polling, ETA, or callback) and IQM reservations: same thread, 2 Jul 2026.
- Kueue admission checks: https://kueue.sigs.k8s.io/docs/concepts/admission_check/

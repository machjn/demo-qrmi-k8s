# QRMI Kubernetes Operator: presentation structure

About 20 minutes. Slides: https://claude.ai/artifact/2Q3uWRhY8JHhJmgYJHVqqj

Assume the audience knows QRMI and Slurm better than Kubernetes.

| # | Section | Slides | Time |
|---|---------|--------|------|
| 1 | The problem | 1–5 | ~4 min |
| 2 | Kubernetes and operators | 6–10 | ~6 min |
| 3 | How the QRMI operator works | 11–18 | ~6 min |
| 4 | Demo video | 19 | ~3 min |
| 5 | Open questions | 20–21 | ~1 min + questions |

## 1. The problem (~4 min)

1. **Cover**: QRMI Kubernetes Operator.
2. **A quantum job needs three things**: a backend, credentials for it, and exclusive or tracked access (QRMI's acquire/release). Who manages these on a shared cluster?
3. **QRMI: one interface, many backends**: recap. acquire and release are the calls that matter here; the task calls are what the workload uses. Table of supported backends (these names reappear in the QuantumResource YAML).
4. **On Slurm, the SPANK plugin does this**: the admin owns the config, the user names a QPU, the plugin acquires and releases around the job. This is the model to copy; almost every piece of the operator maps back to it.
5. **Kubernetes has no equivalent**: lots of compute (GPU/ML) runs on Kubernetes. Without a SPANK role, credentials get baked into images or copied into every namespace, and nothing guarantees a release.

## 2. Kubernetes and operators (~6 min)

6. **The building blocks**: pods, nodes, namespaces, API server. Slurm mapping: nodes are nodes, a pod is roughly a job step, a namespace is a bit like an account or partition.
7. **Everything is an API resource**: you submit desired state, not commands. Nothing runs at apply time; controllers do the work afterwards and keep doing it.
8. **The reconciliation loop**: observe declared state, observe actual state, act to close the gap, repeat. Level-triggered, so restarts and missed events are fine. The QRMI operator is just two of these loops. *The one idea to take away from this section.*
9. **Jobs, Secrets and envFrom**: Jobs outlive their pods, so a QPU lock can't be tied to the Job's lifetime. envFrom turns every key of a Secret into an env var, and QRMI already reads its config from env vars.
10. **An operator teaches Kubernetes a new noun**: a CRD adds a type to the API; a controller gives it behaviour. cert-manager as the classic example.

## 3. How the QRMI operator works (~6 min)

11. **What makes this non-trivial**: the four challenges from `DESIGN_OUTLINE.md`. Each shapes a part of the design: Jobs as the target workload, claims with their own lifecycle, finalizers for clean-up, annotations so users don't write claims.
12. **Two custom resources, one annotation**: the whole design on one slide, with the one-to-one Slurm/SPANK mapping along the bottom.
13. **QuantumResource: the admin's catalogue**: the real manifest from `deploy/resources`. Backend, QRMI type, plain config, and a reference to the API key Secret in the operator namespace.
14. **QuantumResourceClaim: a tracked request** (method A): names a QuantumResource and an optional TTL. Phases Pending → Bound → Released. Failed is planned but not wired up yet.
15. **The happy path, step by step**: Job controller and Claim controller in one binary. The finalizer goes on before acquire, so deleting the claim always triggers release. This is what demo B shows.
16. **Most users only add one annotation** (method B): `quantum.qrmi.io/resource`, plus the Secret name the Job reads (`qrc-job-<job>`). The Job is suspended until the Secret exists.
17. **Clean-up is guaranteed by a finalizer**: four ways a claim gets deleted (Job finished, TTL, owning Job garbage-collected, manual delete). Whatever the trigger, release runs before the claim disappears, so a QPU lock can't leak.
18. **Credentials in one place, Jobs anywhere**: admin credentials live only in `platform-qrmi`; Jobs in any namespace get their own per-Job Secret. Note that the per-Job Secret contains the credentials (leads into the open questions).

## 4. Demo (~3 min)

19. **A real job on a kind cluster**: the video, run against the Felis emulator `ab_emu_1q_lescanne_2020`.
    - **A · Explicit claim**: apply a QuantumResourceClaim, watch it go Pending → Bound, apply a Job that reads its Secret.
    - **B · Annotated Job**: apply one Job; the operator suspends it, claims, unsuspends, and releases when it completes.
    - Use a split terminal with jobs and claims being watched, so the reconciliation loop is visible live. In B, point out the Job sitting suspended until the claim turns Bound.

## 5. Open questions (~1 min + questions)

20. **Open questions** (from the design outline):
    - Access control: anyone who can create a claim gets the credentials. How does that compare with Slurm?
    - A Failed phase: cap retries so bad config doesn't retry acquire() forever.
    - Typed resources: explicit fields per backend rather than passing env vars through?
    - Webhook or Secret: a mutating webhook could add envFrom for the user, at the cost of managing TLS certificates.
    - High availability: the operator runs as a single replica today.
    - Rust or Go: kube-rs uses QRMI directly; Go is the operator norm, via QRMI's C bindings.
21. **Questions?**: point people at `operator/DESIGN_OUTLINE.md` and `operator/DIAGRAMS.md`.

# Vanessa Sochat (LLNL): feedback on the QRMI k8s operator

Summary of Slack discussions, June–July 2026. Vanessa works on Flux and fluence, a custom Kubernetes scheduler plugin backed by Fluxion, and on qrmi-go.

Sources:
- `#slurm-development`:
  - [operator PoC thread](https://alice-et-bob.slack.com/archives/C08ETNQDZK4/p1780594025287249) (6–8 Jun)
  - [co-scheduling critique](https://alice-et-bob.slack.com/archives/C08ETNQDZK4/p1782838975148349) (30 Jun, ~100 replies)
  - [paper thread](https://alice-et-bob.slack.com/archives/C08ETNQDZK4/p1782831566914099) (1–2 Jul)
  - ["more questions about the design"](https://alice-et-bob.slack.com/archives/C08ETNQDZK4/p1782957843614699) (2 Jul)
  - [Job/suspend thread](https://alice-et-bob.slack.com/archives/C08ETNQDZK4/p1783017191930399) (2 Jul)
- Group DM with Yoonho and milroy (29 Jun), about the paper section.

## 1. Credentials should belong to the user, not the cluster

This is her main and most persistent objection.

- **Original question:** a cluster-scoped `QuantumResource` with a hard-coded `secretRef` means one backend definition, one secret and one namespace. How does that handle multi-tenancy? Her instinct is that credentials belong at the level of the user application and shouldn't be owned or even seen by anything above it.
- **The operator calling `acquire()`:** this has the same problem. One controller managing many sessions and injecting secrets means the token leaves the user's application space. She thinks the application should make the call.
- **"Design anti-pattern":** putting credentials in a system space is an anti-pattern. In every place she has worked (Duke, Stanford, LLNL), users have their own credentials, even on institution-funded projects. "A shared credential is not a thing, let alone one for multi tenancy of many users." Admin-owned keys would not be acceptable at LLNL.
- **Design pushes behaviour:** "if you give them a plugin design that requires single, admin-owned access keys, they are going to have the push to do exactly that."
- **Consequences of shared keys:** these came up in the Slurm/SPANK discussion but apply equally to the operator.
  - Any workload holding the cluster key can query the API for **other users' results**. With Braket, that means shared access to an institutional S3 bucket.
  - Credentials passed as env vars are readable from `/proc/self/environ`.
  - There is no fair-share or quota for QPUs: nothing stops one user hogging a device or "racking up a bill of 10K". Billing alerts don't solve this, because token scope and improper charges are separate problems.
  - Someone who owns the account doesn't abuse it. Someone using an institutional account feels less of that burden.
- **Her two models** (her summary for the paper):
  - *Cluster level:* admins manage the accounts and how users interact with the centre-owned tokens. Care is needed to ensure fair share and prevent abuse.
  - *User level:* credentials are private and specific to each user.
- **Paper wording:** she objected to the draft presenting central credential management as a *good* idea ("Oh, managing credentials is so hard!"). She restored a line in the fluence section: "this design does not put any credentials in the system space."

My reply at the time: the operator assumes an HPC admin owns the backend credentials and gives users ready-to-go, acquired resources, which is the same model as Slurm/SPANK. If users own the credentials, QRMI adds less value, because they could mount a secret and call acquire themselves. She agreed the model "makes sense to mirror Slurm". I later said the real question is which ownership models we want to support, and agreed that if users own credentials, acquiring sessions can just as well happen inside workloads.

Ohtani's position: QRMI doesn't require admin-held credentials. `qrmi_config.json` is for cluster-wide cases (e.g. RPI's shared MinIO key), and users can supply their own env vars. Yoonho: "nothing in our design prevents user credentials."

## 2. This isn't co-scheduling; it's a submission wrapper

- **`acquire()` reserves nothing:** for IBM Direct Access it returns a dummy UUID and `release()` is a no-op. For Qiskit Runtime it opens a session, and the first job in a session still waits in the normal vendor queue, possibly for 30+ minutes. Meanwhile the classical resources sit idle.
- **"Second queue":** Slurm, or the operator, can only co-schedule resources it controls. The QPU queue is owned by the vendor, and once work leaves the cluster the scheduler has no control over it.
- **What the operator actually adds:** at most accounting or gatekeeping. It acts as an "authentication wrapper and API proxy" that hands off to an independent vendor queue.
- **Workflow vs workload managers:** opening a session and submitting tasks belongs to the application, so to a workflow tool (Snakemake, Nextflow…), not to a workload manager. A workload manager adds value *if* it intelligently coordinates quantum with classical resources.
- **What she wants from vendors:**
  - "reserve me a slot and send an event when I'm at the top of the queue";
  - the ability to get into the queue without starting the application;
  - polling queue depth would do for now.
  - Ohtani agreed, citing IQM's reservation API, and said the right shape for this in QRMI is still open.
- **Fluence, her alternative:**
  - pods are *gated*: they don't consume resources while gated;
  - a sidecar in the leader pod watches the vendor queue and ungates the workers when the task reaches position 1;
  - credentials stay in user space;
  - her experiments show much less idle time for classical workers and more predictable runtimes.
- **Scheduling, not execution:** acquiring and checking quantum resources should happen during *scheduling*, before classical resources are provisioned. Then a failure leaves the job pending ("no harm done"), rather than cancelling or draining jobs, which during a remote API outage would be like a cluster DDoS.
- **On the operator specifically:**
  - Suspend waits on `acquire()`, which doesn't wait for the queue; it just returns a UUID.
  - "All the suspend is giving you is a guarantee to not dispatch pods if the qrmi endpoint is not alive."
  - She suggested tying suspend to *device availability* instead: "if you encourage users to use Job, and then link suspend to when the device is available, it will work great."
  - (Yoonho's paper question was in the same spirit: can claims carry a QPU ready/not-ready status? I proposed marking a claim `Bound` only once the resource is actually ready.)

## 3. Only Jobs can be annotated: suspend doesn't generalise

- **Her point:** requiring a Job abstraction to get `suspend` means other CR types that need QPUs, and that can't be suspended, won't work. She deliberately didn't use Job in fluence for this reason.
- **Her alternative:** **pod scheduling gates**, which work for every abstraction because Pods are the base unit. Doing this generally needs "some thin layer in the scheduler": in practice, webhooks plus controllers. Fluence uses a webhook with a custom-resource provider interface (gangs and quantum so far).
- **Kubernetes trends:**
  - native gang scheduling (PodGroup) from 1.36.x, available on kind 1.36.2 and GKE but not yet on AWS or Azure;
  - a second workload queue is coming;
  - beware the two-scheduler problem: a custom scheduler's view diverges from the default scheduler's.
- **Long-lived workloads:** I raised that the "acquire before scheduling" model doesn't suit Deployments or StatefulSets, which may need QPUs at many points in their lifetime. Her view: don't assume anything about an abstraction's lifecycle, because Deployments can be ephemeral too. The pod-level view covers them.
- **My response at the time:** "completely agree reliance on suspend is a weak point in terms of extensibility… wasn't sure how to gate pods without control of the scheduler."

## 4. Paper and wording advice

- **Avoid "native"** for the operator, since native means part of `kubernetes/kubernetes`. Describe exactly what it is: custom resources and a controller. ("Kubernetes Integration with an Operator" was the heading Yoonho proposed.)
- **No value judgements:** describe the design and let the reader decide. "When you make a strong, opinionated value statement about design that a reviewer disagrees with… you lose their trust."
- **Terminology:** job manager ≠ resource manager ≠ workload manager. A workload manager (Slurm, PBS, Flux) is the umbrella. A workflow manager is a different thing, and uses a workload manager as its executor.
- **Typo:** the PoC docs described `suspend: true` the wrong way round. When suspend is true the Job controller *does* hold off creating pods.

## Takeaways for the SC26 talk

- **Anticipate the credentials question.** Present the operator as implementing the *cluster-admin* credential model, the same one as SPANK. Acknowledge the user-owned model, and that QRMI doesn't preclude it.
  - Possible extension: let a claim reference a Secret in the *user's* namespace instead of the admin one.
- **Be precise about scope.** The operator manages the *lifecycle* of acquire/release and credential hand-off. It does not co-schedule, and pods can still wait on the vendor queue. Real co-scheduling needs vendor support (reservations or queue events) plus scheduler integration.
- **Jobs only:** say so explicitly, and why (suspend). Mention scheduling gates, or a webhook plus gates, as the route to generalise.
- **Open questions slide** overlaps a lot with this: access control, a `Failed` phase or retry cap, a webhook. Consider adding:
  - fair-share and quotas;
  - binding claims on actual device readiness rather than `acquire()` success.
- Possibly name-check fluence as the complementary approach, since Vanessa is presenting Flux straight after the talk.

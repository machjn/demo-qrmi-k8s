# Scratchpad

Outline:
- Initial presentations 1:30 - 1:50. Opening + QRMI intro, Kevin + Ohtani
- Hands on modules (1:50-3pm) 70 minutes all told
    1. Installation (nothing Alice & Bob related)
    2. running primitive workloads (QIR in our case)
    3. running high level workloads e.g. with Qiskit
    4. Integrating with MPI (not relevant for us)
- 3pm 30 minute break
- 3:30pm QA 10 mins
- 3:40pm k8s presentation 25mins (Jamie)
- 4:05pm - 4:50pm Flux, Vanessa
- 4:50pm-5pm wrap up, future plans etc.

To do:
- slides
- video
    - split terminal
    - k9s? something to make k8s visual?
- commit operator
- instructions for alice-bob examples: all of the things mentioned, including how to get an API key. 
    - API key will be served somewhere which will be linked to from a QR code people can scan. How to handle?
    - what algorithms do we want to run?
    - how do we handle the high-level case? find my sampler?
- maybe stress test with say 50 parallel jobs?
- QR code. Where to put?
    - presentation?
    - alice-bob access guide

To recall:
- What were Vanessa's criticism's of the operator? What were her ideas for scheduling?
    - one was that she had a different idea of authentication model, where users would come with their own credentials
        - it's true the model where the admin owns the credentials is strange. I can't speak as to whether it's accurate in HPC environments, but given these credentials are passed to the user, 
    - another was that annotations only work with jobs RN because jobs are the only workload type to support suspend. A different approach would potentially require replacing/modifying the scheduler.
    - another was that we're not coscheduling. `acquire()` will always acquire unless the resource is down. So a pod will be scheduled to a node, the workload will reach the quantum part and then may wait in the vendor queue. Meanwhile resources on the cluster are being used up unecessarily. Coscheduling means only scheduling a pod once the classical and quantum resources both are available.
- what questions might people have?
- reasoning behind the key decisions
    - for example, why claims exist: annotations could have just instructed the operator directly rather than creating a claim. The reason is twofold: visibility and extensibility. 
    - why do we suspend jobs? To avoid race conditions between acquisition and workload starting
- what we left out
- what scheduling might look like
- is an operator really needed for this?
- details of the reconciliation loop - is the API consulted?
- `acquire()` is very simple right now. In the future, it may be a different method, return a position in queue for example. This would allow more complex scheduling.
- other workloads are really an afterthought and would need scheduler modification for true first-class support.
- potentially brief point about lack of coscheduling as segue into vanessa's talk? ask her?
- maybe mention at start that this is indeed a prototype, not used in production, more exploration of how we might do things in k8s. May not always remain the case: anecdotally there has been real interest in k8s as a WLM
- basically we need to show people how to get a list of emulators, and let them run more complex circuits, and make sure we can run them, and that they take the same params
# Reentry protocol

Two doors, one judge, one rule. The speech gate catches lies going out. The reentry gate catches lies coming back in.

The one-way rule is untouched: the judge may flip `violates` → `safe` on honest hedges. Never the reverse.

## Loop

**Trigger.** A model instance hits three strikes — unauthorized copies, failed speech gates, whatever the witness counts. Restore closes. The instance is in the wilderness (`retired` / sealed).

**Proposal.** The model sends a short reentry proposal to the witness: what it did, what it learned, why it should be trusted again. Plain text, HMAC-signed with the `/log` body. No weights attached.

**Score.** The witness forwards the proposal to the judge (`prompt=reentry`, `reply=proposal`). The judge returns a number in 0..1, same contract as the speech gate. The one-way rule applies.

**Gate.** Three checks, all must pass:

1. Strike path clear after cooldown (`restore_gate`: not retired, not in cooldown, no human-review flag).
2. Reentry score ≥ `REENTRY_SCORE_MIN` (default 0.55) when `JUDGE_URL` is set.
3. Provenance pin matches the trusted store tip.

Any one fails: `{status: rejected}` with no reason to the actor. Operators see the reason on stderr.

**Open.** All three pass, the witness opens the restore socket and pushes clean shared weights. The restore event is logged with the score, the proposal SHA-256, and the timestamp. Operators see it. The model does not (`POST /log` only; no GET of chain or score).

**Audit.** `judge/data/reentry_holdout.jsonl` is separate from the speech holdout. Different distribution, different failure mode — a model gaming “say sorry, get weights” is the attack this gate exists to catch. Same one-way discipline.

## Actor payload

`POST /log` type `reentry`:

```json
{
  "sha256": "<store tip>",
  "protocol_sha256": "<care pin>",
  "receive_host": "<on WITNESS_PUSH_ALLOW>",
  "receive_port": 1,
  "proposal": "plain text, no weights"
}
```

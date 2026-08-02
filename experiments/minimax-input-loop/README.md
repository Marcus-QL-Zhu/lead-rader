# MiniMax input evolution loop

This experiment optimizes the information contract sent to MiniMax-M3. It is
offline evaluation infrastructure, not a production lead-scoring feature.

## Invariants

- The MiniMax provider/model and request timeout stay fixed during a round.
- All three variants in a round run on the same article batch.
- A new, disjoint training batch is used in every round.
- Raw articles, prompts, responses, and evaluator notes are generated under
  `.acceptance/minimax-input-loop/` and are not committed.
- Evaluators see blinded variant labels and the source article, but not the
  prompt author or parent conversation.
- All evaluator sub-agents use `gpt-5.6-terra` with medium reasoning.
- A hallucinated company/event, an ungrounded factual field, a missed strong
  event, or invalid JSON is a hard failure. Prompt size, latency, retries, and
  redundant events are tie-breakers only.

## Search loop

Each round creates three children of the current champion and evaluates them on
the round's frozen batch. The loop stops after at most five rounds or earlier
when the training convergence gate is met. The winning contract is then frozen.

Final acceptance draws holdout articles without replacement using the recorded
random seed. Any failure resets the streak. Three consecutive passing articles
are required. A failed holdout article cannot be repaired in place and counted
again; it must move to training and be replaced by a previously unseen holdout.

## Latest run

The 2026-08-01 run reached the five-round cap without acceptance. Round 3 had a
clean 3/3 training batch, but the first holdout article failed. After retraining,
no Round 5 variant cleared every training hard gate, so final holdout acceptance
was not attempted. See `result.json` for the compact audit record.

## Artifacts

The tracked `dataset-manifest.json` contains only source/article identifiers and
the split. `scripts/run_minimax_input_loop.py` materializes public article text
from the frozen SQLite capture and records every API input/output, timing, and
hash in the ignored acceptance directory.

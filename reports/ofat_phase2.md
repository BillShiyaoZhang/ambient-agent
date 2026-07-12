# Routing Experiment Report

_Generated: 2026-07-11T13:55:34Z_

_Provider: minimax, Model: MiniMax-M3, Repeats: 3_

## Summary

| Variant | Score | Kind Acc | App ID Acc | Stability | Ambig Kind Acc | Passes |
|---|---|---|---|---|---|---|
| V_C1C | 1.667 | 95% | 83% | 100% | 75% | ✅ |
| V_C2B | 1.667 | 95% | 83% | 100% | 75% | ✅ |
| V_C2C | 1.667 | 95% | 83% | 100% | 75% | ✅ |
| V_C3B | 1.667 | 95% | 83% | 100% | 75% | ✅ |
| V_C3C | 1.667 | 95% | 83% | 100% | 75% | ✅ |
| V_C4B | 1.667 | 95% | 83% | 100% | 75% | ✅ |
| V_C5C | 1.667 | 95% | 83% | 100% | 75% | ✅ |
| V_C6B | 1.667 | 95% | 83% | 100% | 75% | ✅ |
| V_C7B | 1.667 | 95% | 83% | 100% | 75% | ✅ |
| V_C3A | 1.631 | 93% | 81% | 98% | 67% | ✅ |
| V_C4A | 1.631 | 93% | 81% | 98% | 67% | ✅ |
| V_C5A | 1.631 | 93% | 81% | 98% | 67% | ✅ |
| V_C5B | 1.631 | 93% | 81% | 98% | 67% | ✅ |
| V_C6A | 1.631 | 93% | 81% | 98% | 67% | ✅ |
| V_C7A | 1.631 | 93% | 81% | 98% | 67% | ✅ |
| V_C7C | 1.631 | 93% | 81% | 98% | 67% | ✅ |
| V_C1A | 1.617 | 93% | 78% | 98% | 67% | ✅ |
| V_C2A | 1.617 | 93% | 78% | 98% | 67% | ✅ |
| V_baseline | 1.609 | 92% | 81% | 97% | 67% | ✅ |
| V_C6C | 1.596 | 92% | 78% | 97% | 58% | ❌ |
| V_C1B | 1.546 | 87% | 78% | 97% | 33% | ❌ |
| V_C4C | 1.543 | 88% | 75% | 95% | 50% | ❌ |


## Per-Choice Winner (OFAT)

| Choice | Winner Variant | Score | Kind Acc | App ID Acc | Stability | Ambig Kind Acc |
|---|---|---|---|---|---|---|
| C1 | V_C1C | 1.667 | 95% | 83% | 100% | 75% |
| C2 | V_C2B | 1.667 | 95% | 83% | 100% | 75% |
| C3 | V_C3B | 1.667 | 95% | 83% | 100% | 75% |
| C4 | V_C4B | 1.667 | 95% | 83% | 100% | 75% |
| C5 | V_C5C | 1.667 | 95% | 83% | 100% | 75% |
| C6 | V_C6B | 1.667 | 95% | 83% | 100% | 75% |
| C7 | V_C7B | 1.667 | 95% | 83% | 100% | 75% |


## Ambiguous Scenario Drill-Down

| Scenario | Message | Expected | Per-variant accuracy |
|---|---|---|---|
| S04 | 建一个时钟 | widget_modify | V_baseline=100%, V_C1A=100%, V_C1B=0%, V_C1C=100%, V_C2A=100%, V_C2B=100% ... |
| S05 | 重新做一个待办 | widget_modify | V_baseline=67%, V_C1A=67%, V_C1B=33%, V_C1C=100%, V_C2A=67%, V_C2B=100% ... |
| S08 | 在待办里加买牛奶 | widget_modify | V_baseline=0%, V_C1A=0%, V_C1B=100%, V_C1C=0%, V_C2A=0%, V_C2B=0% ... |
| S17 | add buy milk to todos | graph_mutation | V_baseline=100%, V_C1A=100%, V_C1B=0%, V_C1C=100%, V_C2A=100%, V_C2B=100% ... |


## Recommendation

Apply the winning letter for each choice:

- **C1** → `C` (score 1.667)
- **C2** → `B` (score 1.667)
- **C3** → `B` (score 1.667)
- **C4** → `B` (score 1.667)
- **C5** → `C` (score 1.667)
- **C6** → `B` (score 1.667)
- **C7** → `B` (score 1.667)


## Thresholds

- Min stability: 80%
- Min ambiguous scenario kind accuracy: 60%

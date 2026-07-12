# Routing Experiment Report

_Generated: 2026-07-11T23:06:46Z_

_Provider: minimax, Model: MiniMax-M3, Repeats: 3_

## Summary

| Variant | Score | Kind Acc | App ID Acc | Stability | Ambig Kind Acc | Passes |
|---|---|---|---|---|---|---|
| V_C2B | 1.755 | 100% | 91% | 100% | 100% | ✅ |
| V_C2C | 1.755 | 100% | 91% | 100% | 100% | ✅ |
| V_C5A | 1.755 | 100% | 91% | 100% | 100% | ✅ |
| V_C4B | 1.733 | 98% | 91% | 98% | 100% | ✅ |
| V_C1A | 1.718 | 98% | 88% | 98% | 92% | ✅ |
| V_C1C | 1.718 | 98% | 88% | 98% | 92% | ✅ |
| V_C2A | 1.718 | 98% | 88% | 98% | 92% | ✅ |
| V_C3B | 1.718 | 98% | 88% | 98% | 92% | ✅ |
| V_C3C | 1.718 | 98% | 88% | 98% | 92% | ✅ |
| V_C5B | 1.718 | 98% | 88% | 98% | 92% | ✅ |
| V_C5C | 1.711 | 97% | 91% | 97% | 92% | ✅ |
| V_C4C | 1.696 | 97% | 88% | 97% | 92% | ✅ |
| V_C7A | 1.696 | 97% | 88% | 97% | 83% | ✅ |
| V_baseline | 1.686 | 97% | 85% | 98% | 83% | ✅ |
| V_C3A | 1.686 | 97% | 85% | 98% | 83% | ✅ |
| V_C7C | 1.686 | 97% | 85% | 98% | 83% | ✅ |
| V_C7B | 1.681 | 97% | 85% | 97% | 92% | ✅ |
| V_C4A | 1.664 | 95% | 85% | 97% | 83% | ✅ |
| V_C6C | 1.627 | 93% | 82% | 95% | 83% | ✅ |
| V_C1B | 1.459 | 80% | 73% | 98% | 0% | ❌ |
| V_C6A | 1.123 | 55% | 55% | 100% | 50% | ❌ |
| V_C6B | 0.982 | 50% | 36% | 100% | 25% | ❌ |


## Per-Choice Winner (OFAT)

| Choice | Winner Variant | Score | Kind Acc | App ID Acc | Stability | Ambig Kind Acc |
|---|---|---|---|---|---|---|
| C1 | V_C1A | 1.718 | 98% | 88% | 98% | 92% |
| C2 | V_C2B | 1.755 | 100% | 91% | 100% | 100% |
| C3 | V_C3B | 1.718 | 98% | 88% | 98% | 92% |
| C4 | V_C4B | 1.733 | 98% | 91% | 98% | 100% |
| C5 | V_C5A | 1.755 | 100% | 91% | 100% | 100% |
| C6 | V_C6C | 1.627 | 93% | 82% | 95% | 83% |
| C7 | V_C7A | 1.696 | 97% | 88% | 97% | 83% |


## Ambiguous Scenario Drill-Down

| Scenario | Message | Expected | Per-variant accuracy |
|---|---|---|---|
| S04 | 建一个时钟 | widget_modify | V_baseline=100%, V_C1A=100%, V_C1B=0%, V_C1C=100%, V_C2A=100%, V_C2B=100% ... |
| S05 | 重新做一个待办 | widget_modify | V_baseline=33%, V_C1A=67%, V_C1B=0%, V_C1C=67%, V_C2A=67%, V_C2B=100% ... |
| S08 | 在待办里加买牛奶 | graph_mutation | V_baseline=100%, V_C1A=100%, V_C1B=0%, V_C1C=100%, V_C2A=100%, V_C2B=100% ... |
| S17 | add buy milk to todos | graph_mutation | V_baseline=100%, V_C1A=100%, V_C1B=0%, V_C1C=100%, V_C2A=100%, V_C2B=100% ... |


## Recommendation

Apply the winning letter for each choice:

- **C1** → `A` (score 1.718)
- **C2** → `B` (score 1.755)
- **C3** → `B` (score 1.718)
- **C4** → `B` (score 1.733)
- **C5** → `A` (score 1.755)
- **C6** → `C` (score 1.627)
- **C7** → `A` (score 1.696)


## Thresholds

- Min stability: 80%
- Min ambiguous scenario kind accuracy: 60%

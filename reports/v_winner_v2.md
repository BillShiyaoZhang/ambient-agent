# Routing Experiment Report

_Generated: 2026-07-11T23:12:39Z_

_Provider: minimax, Model: MiniMax-M3, Repeats: 5_

## Summary

| Variant | Score | Kind Acc | App ID Acc | Stability | Ambig Kind Acc | Passes |
|---|---|---|---|---|---|---|
| V_winner | 1.675 | 96% | 85% | 96% | 95% | ✅ |


## Per-Choice Winner (OFAT)

| Choice | Winner Variant | Score | Kind Acc | App ID Acc | Stability | Ambig Kind Acc |
|---|---|---|---|---|---|---|


## Ambiguous Scenario Drill-Down

| Scenario | Message | Expected | Per-variant accuracy |
|---|---|---|---|
| S04 | 建一个时钟 | widget_modify | V_winner=80% |
| S05 | 重新做一个待办 | widget_modify | V_winner=100% |
| S08 | 在待办里加买牛奶 | graph_mutation | V_winner=100% |
| S17 | add buy milk to todos | graph_mutation | V_winner=100% |


## Recommendation

_No winner data yet._


## Thresholds

- Min stability: 80%
- Min ambiguous scenario kind accuracy: 60%

# Routing Experiment Report

_Generated: 2026-07-11T13:04:09Z_

_Provider: minimax, Model: MiniMax-M3, Repeats: 2_

## Summary

| Variant | Score | Kind Acc | App ID Acc | Stability | Ambig Kind Acc | Passes |
|---|---|---|---|---|---|---|
| V_baseline | 1.800 | 100% | 100% | 100% | 100% | ✅ |


## Per-Choice Winner (OFAT)

| Choice | Winner Variant | Score | Kind Acc | App ID Acc | Stability | Ambig Kind Acc |
|---|---|---|---|---|---|---|


## Ambiguous Scenario Drill-Down

| Scenario | Message | Expected | Per-variant accuracy |
|---|---|---|---|
| S04 | 建一个时钟 | widget_modify | V_baseline=100% |
| S05 | 重新做一个待办 | widget_modify |  |
| S08 | 在待办里加买牛奶 | widget_modify |  |
| S17 | add buy milk to todos | graph_mutation |  |


## Recommendation

_No winner data yet._


## Thresholds

- Min stability: 80%
- Min ambiguous scenario kind accuracy: 60%

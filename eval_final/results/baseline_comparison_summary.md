# Baseline Comparison Summary

Method-wise macro averages on the same 20 real commit cases.

| Method | Precision | Recall | F1 | Avg Tokens |
|---|---:|---:|---:|---:|
| dgrag | 0.9113 | 1.0000 | 0.9453 | 344.8 |
| diff_only | 0.0000 | 0.0000 | 0.0000 | 247.9 |
| file_context | 0.0741 | 0.3400 | 0.1216 | 11369.8 |
| semantic_proxy | 0.0275 | 0.1100 | 0.0440 | 7910.9 |

## Context Reduction vs File Context
- `dgrag`: 0.9697
- `diff_only`: 0.9782
- `file_context`: 0.0000
- `semantic_proxy`: 0.3042

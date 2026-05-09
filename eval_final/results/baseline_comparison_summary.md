# Baseline Comparison Summary

Method-wise macro averages on the same 50 real commit cases.

| Method | Precision | Recall | F1 | Avg Tokens |
|---|---:|---:|---:|---:|
| dgrag | 0.7779 | 1.0000 | 0.8454 | 527.0 |
| diff_only | 0.0000 | 0.0000 | 0.0000 | 374.1 |
| file_context | 0.1836 | 0.5440 | 0.2555 | 14497.1 |
| semantic_proxy | 0.0370 | 0.1780 | 0.0603 | 15992.2 |

## Context Reduction vs File Context
- `dgrag`: 0.9636
- `diff_only`: 0.9742
- `file_context`: 0.0000
- `semantic_proxy`: -0.1031

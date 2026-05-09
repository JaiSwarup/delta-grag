import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.resolve()))

from src.eval.runner import run_eval, EvalRunConfig

def main():
    print("Running D-GRAG evaluation on ContextCR configs...")
    config = EvalRunConfig(
        configs_dir="evaluate/configs/contextcr",
        systems=("dgrag", "diff_only", "file_context", "semantic_rag"),
        # limit=2,
    )
    df = run_eval(config)
    print("\nEvaluation complete. Results summary:")
    print(df[['pr_id', 'system', 'structural_recall', 'precision', 'token_reduction_pct']].to_markdown())

if __name__ == "__main__":
    main()

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

def generate_yaml(repo_name, commits, output_dir):
    """Generate an EvalRepoConfig YAML string for a single repository."""
    # Convert 'huggingface/transformers' to 'transformers'
    short_name = repo_name.split("/")[-1]
    url = f"https://github.com/{repo_name}"
    
    yaml_lines = [
        f"name: {short_name}",
        f"url: {url}",
        "language: python",
        "size_category: large",
        "test_commits:"
    ]
    
    # Take up to 10 commits to keep evaluation fast
    for commit in commits[:10]:
        yaml_lines.append(f"  - sha: {commit['sha']}")
        # Clean description
        desc = commit['description'].replace('"', "'").replace("\n", " ").strip()
        if desc:
            yaml_lines.append(f"    description: \"{desc}\"")
            
    # Write to file
    out_path = Path(output_dir) / f"{short_name}.yaml"
    out_path.write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    return out_path

def main(input_dir, output_dir):
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    if not input_path.exists():
        print(f"Error: Input directory {input_dir} not found.")
        sys.exit(1)
        
    print(f"Scanning {input_dir} for Python commits...")
    
    repo_commits = defaultdict(list)
    
    # Only read up to a certain limit of files just to prevent massive memory usage, or read all
    for i, file_name in enumerate(os.listdir(input_path)):
        if i >= 60000:
            print("Reached limit of 60000 files. Stopping...")
            break
        if i % 5000 == 0:
            print(f"Processed {i} files...")
        if not file_name.endswith('.json'):
            continue
            
        file_path = input_path / file_name
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            continue
            
        if data.get("lang", "").lower() != "python":
            continue
            
        repo = data.get("full_name")
        sha = data.get("commit_id")
        desc = data.get("pr_title", data.get("issue_title", ""))
        
        if repo and sha:
            # Deduplicate by SHA to avoid repeating the same commit multiple times
            existing_shas = {c["sha"] for c in repo_commits[repo]}
            if sha not in existing_shas:
                repo_commits[repo].append({
                    "sha": sha,
                    "description": desc
                })
            
    print(f"Found {sum(len(c) for c in repo_commits.values())} Python commits across {len(repo_commits)} repos.")
    
    # Sort repos by number of commits
    sorted_repos = sorted(repo_commits.items(), key=lambda x: len(x[1]), reverse=True)
    
    # Process the top 5 repos for now
    for repo_name, commits in sorted_repos[:5]:
        out_file = generate_yaml(repo_name, commits, output_dir)
        print(f"Generated {out_file} with {min(10, len(commits))} commits (from {len(commits)} total available).")
        
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Convert ContextCRBench JSONs to BTP YAML configs.")
    parser.add_argument("--input", default=r"C:\Users\jaisw\Desktop\projects\ai-code-review\final_json")
    parser.add_argument("--output", default=r"C:\Users\jaisw\Desktop\projects\btp\evaluate\configs\contextcr")
    
    args = parser.parse_args()
    main(args.input, args.output)

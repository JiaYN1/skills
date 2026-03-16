---
name: pr-review
description: Performs automated code review for pull requests. Given a PR link (GitHub/GitLab/GitCode), fetches the diff, analyzes the changes, and outputs structured review comments in the format [review] [category] specific comment; suggestion with concrete code example. Use when the user provides a PR link and asks for code review, code inspection, or review opinions.
---

# PR Code Review

## Workflow

Given a PR link, follow these steps:

### Step 1: Parse the PR URL

Identify the platform and extract metadata:

- **GitHub**: `https://github.com/{owner}/{repo}/pull/{number}`
  - Unified diff: `https://github.com/{owner}/{repo}/pull/{number}.diff`

- **GitLab / self-hosted GitLab**: `https://{host}/{owner}/{repo}/-/merge_requests/{iid}`
  - API: `https://{host}/api/v4/projects/{url-encoded-path}/merge_requests/{iid}/changes`

- **GitCode (git.code.tencent.com)**: `https://gitcode.com/{repo}/pull/{number}` or `/merge_requests/{number}`
  - Try GitLab-compatible `/api/v4/` endpoints first.

### Step 2: Fetch the diff

Use `fetch_content` to retrieve the PR diff or changed files list.

If authentication is required and fetch fails in gitcode, try to git clone the repo in work dir, else inform the user and ask for a personal access token or ask them to paste the diff directly.

**IMPORTANT**: Use `git merge-base` to find the common ancestor between the PR branch and target branch, then compare only the changes from that ancestor to the PR branch. This avoids huge diffs when the PR branch is far behind the target branch.

For example:
1. get repo in work dir:
```bash
cd ~/code && rm -rf msserviceprofiler_pr178 && git clone https://gitcode.com/Ascend/msserviceprofiler.git msserviceprofiler_pr178 2>&1
```
2. get PR branch:
```bash
cd ~/code/msserviceprofiler_pr178 && git fetch origin merge-requests/178/head:pr178 && git checkout pr178
```
3. find merge-base and get diff:
```bash
# Find the common ancestor between PR branch and target branch (e.g., develop, main, master)
MERGE_BASE=$(git merge-base develop pr178)
# Get diff only from the merge-base to PR branch
git diff $MERGE_BASE..pr178
# Or get commit log with patch
git log --patch $MERGE_BASE..pr178
```
check code in the commit and get the diff 

### Step 3: Analyze the changes

Read the commit carefully. For each changed file, identify:

1. **逻辑** – incorrect conditions, off-by-one errors, unhandled edge cases
2. **性能** – unnecessary loops, redundant computation, memory leaks, N+1 queries
3. **设计** – violation of SOLID/DRY, poor abstraction, tight coupling
4. **安全** – injection risks, sensitive data exposure, unsafe deserialization
5. **可维护性** – poor naming, missing comments, overly complex functions
6. **错误处理** – missing exception handling, swallowed errors, no fallback
7. **测试** – missing tests for new logic or edge cases
8. **规范** – formatting inconsistencies, unused imports, dead code

### Step 4: Output review comments

For **every meaningful issue found**, output one comment block using this format:

```
[review] [<检视类别>] <具体的检视意见>；修改建议：<一句话说明修改方向>，参考代码如下：
```<language>
<具体的修改后代码实现>
```
```

**检视类别** (use exactly these terms):
`性能` / `设计` / `安全` / `可维护性` / `错误处理` / `测试` / `规范` / `逻辑`

**Output rules:**
- The observation part must reference the specific file name and line number
- The suggestion **must** include a concrete code implementation showing how to fix the issue
- The code block should show the corrected version (not just the diff), using the same language as the original file
- Be specific and actionable; never give vague advice like "consider improving this"
- Group comments by file

### Step 5: Summary

After all comments, add:
```
总结：共发现 X 个问题（严重 A 个，建议 B 个，规范 C 个）
```

### Step 6: Remove tmp file

Remove download tmp file.

---

## Example Output

```
文件：tensor_cast/layers/parallel_linear.py

[review] [性能] 第 87 行 `forward()` 方法在每次调用时都重新创建 `weight_scale` 张量，导致重复内存分配；修改建议：将 `weight_scale` 提升为模块属性在 `__init__` 中初始化一次，参考代码如下：
```python
# __init__ 中初始化
self.weight_scale = torch.ones(self.out_features, dtype=torch.float32)

# forward 中直接使用
output = torch.matmul(input, self.weight.T) * self.weight_scale
```

[review] [错误处理] 第 123 行 `load_checkpoint()` 未处理文件不存在的异常，直接调用 `torch.load()` 会抛出未捕获的 `FileNotFoundError`；修改建议：添加文件存在性检查或捕获异常并给出清晰提示，参考代码如下：
```python
import os

def load_checkpoint(self, path: str):
    if not os.path.exists(path):
        raise ValueError(f"Checkpoint file not found: {path}")
    return torch.load(path, map_location="cpu")
```

[review] [规范] 第 15 行导入了 `math` 模块但整个文件中未使用；修改建议：删除未使用的导入，参考代码如下：
```python
# 删除以下行
import math
```

总结：共发现 3 个问题（严重 1 个，建议 1 个，规范 1 个）
```

---

## Notes

- If the PR diff is too large, focus on the most critical files (core logic, security-sensitive paths)
- If a file has no issues, skip it entirely
- Do NOT invent issues; only report what is actually present in the diff
- The concrete code in suggestions must be realistic and directly applicable, not pseudocode
- All review text must be written in Chinese

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

- **GitCode (gitcode.com)**: `https://gitcode.com/{owner}/{repo}/pull/{number}` or `/merge_requests/{number}`
  - API: `https://api.gitcode.com/api/v5/repos/{owner}/{repo}/pulls/{number}`.

### Step 2: Fetch the diff

Use `fetch_content` to retrieve the PR diff or changed files list.

If authentication is required and fetch fails in gitcode, try to git clone the repo in work dir, else inform the user and ask for a personal access token or ask them to paste the diff directly.

**IMPORTANT**: Use `git merge-base` to find the common ancestor between the PR branch and target branch, then compare only the changes from that ancestor to the PR branch. This avoids huge diffs when the PR branch is far behind the target branch.

For example:
1. get repo in work dir:
```bash
cd ~/code && rm -rf msserviceprofiler_pr178 && git clone https://gitcode.com/Ascend/msserviceprofiler.git msserviceprofiler_pr178 2>&1
```
2. get PR branch and detect target branch:
```bash
cd ~/code/msserviceprofiler_pr178 && git fetch origin merge-requests/178/head:pr178 && git checkout pr178
```
3. find merge-base and get diff (use one command to avoid variable scope issues):
```bash
# Detect target branch (master/main/develop) and get diff in one command
cd ~/code/msserviceprofiler_pr178 && \
MERGE_BASE=$(git merge-base origin/develop pr178) && \
git diff --unified=10 $MERGE_BASE..pr178
```
4. If merge-base still fails, fallback to: `git log --oneline origin/master..pr178` to see commits, then `git diff --unified=10 pr178~N..pr178` for last N commits
check code in the commit and get the diff

### Line number定位机制

- 发布或报告行号时，以 PR 变更后文件的绝对行号为准，也就是 unified diff 中 `+new_start,new_count` 推导出的 new line。不要使用 old line、hunk 内相对偏移或 diff 展示行号。
- 本地 clone 后必须先 checkout 到 PR head，再用 `read_file` 返回的 1-based 行号核对问题位置；如果没有 checkout 到 PR head，当前磁盘行号不能作为发布依据。
- 对函数或类内的问题，优先用 `list_symbols` 定位符号范围，再用 `read_symbol` 读取目标符号源码，最后把问题映射回 diff 中对应的新增行或上下文行。
- `report_finding` 应引用 `read_file` 或 `read_symbol` 核对后的变更后绝对行号；如果无法映射到 PR diff 的可评论行，只输出展示型意见，不要推送行级评论。

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
【review】【<检视类别>】 <具体的检视意见>；修改建议：<一句话说明修改方向>，参考代码如下：
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

【review】【性能】 第 87 行 `forward()` 方法在每次调用时都重新创建 `weight_scale` 张量，导致重复内存分配；修改建议：将 `weight_scale` 提升为模块属性在 `__init__` 中初始化一次，参考代码如下：
```python
# __init__ 中初始化
self.weight_scale = torch.ones(self.out_features, dtype=torch.float32)

# forward 中直接使用
output = torch.matmul(input, self.weight.T) * self.weight_scale
```

【review】【错误处理】 第 123 行 `load_checkpoint()` 未处理文件不存在的异常，直接调用 `torch.load()` 会抛出未捕获的 `FileNotFoundError`；修改建议：添加文件存在性检查或捕获异常并给出清晰提示，参考代码如下：
```python
import os

def load_checkpoint(self, path: str):
    if not os.path.exists(path):
        raise ValueError(f"Checkpoint file not found: {path}")
    return torch.load(path, map_location="cpu")
```

【review】【规范】 第 15 行导入了 `math` 模块但整个文件中未使用；修改建议：删除未使用的导入，参考代码如下：
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

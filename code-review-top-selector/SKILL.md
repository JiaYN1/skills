---
name: code-review-top-selector
description: Filter Top N high-quality code review comments from CSV, TSV, or XLSX files. Supports deterministic grading or optional OpenAI-compatible AI grading for each review comment's type and quality grade (a/b/c/d), then selects the highest-grade comments. Use when processing code review spreadsheet files, filtering high-quality review comments, or generating review reports.
---

# Code Review Top Selector

## Overview

This skill filters the Top N high-quality code review comments from a CSV, TSV, or XLSX file and outputs a new CSV file.

### Quality Grade Standards

- **Grade a**: Contains only inquiries or pure restatement of coding standards
  - Example: "Single line code should not exceed 80 characters"
- **Grade b**: Clearly identifies the problem and states what to do
  - Example: "Suggest using relative paths instead of absolute paths"
- **Grade c**: Demonstrates thinking and explains how to design the code
  - Example: "This flag is not meaningful and increases cyclomatic complexity. If the condition is not met, just log it and return failure."
- **Grade d**: Builds on grade c by explaining why or how to improve
  - Example: Detailed problem analysis + specific improvement plan + code examples

### Filtering Strategy

1. **Validity Check**: 
   - Only process comments whose content contains `review`
   - Ignore all other comments (considered invalid)

2. **Deduplication**: 
   - Remove duplicate review comments based on content
   - Keep only unique review comments
   - When duplicates exist, keep the first occurrence
   - Deduplication happens before deterministic or AI scoring

3. **Priority Filtering**: 
   - Prioritize d-grade comments, then c/b/a until Top N is filled
   - If grades are equal, prefer longer review comments
   - `limit` controls the output Top N count

4. **Type Identification**:
   - Extract tags from content (e.g., 【review】【安全】)
   - If no explicit tags, AI determines type based on content

## Usage

### Method: AI-Assisted Processing

Tell the AI: "Please use the code-review-top-selector skill to process this CSV/XLSX file"

The AI will process **each review comment one by one**:

1. Read the CSV/XLSX file
2. **For each review comment:**
   - **Check validity**: Only process if the content contains `review`, skip others
   - **Deduplicate before scoring**: Track seen comments, skip duplicates
   - Analyze the review content
   - **Determine quality grade** (a/b/c/d) based on the standards
   - **Identify review type** (security/performance/standard/design/review/etc.)
3. After analyzing all unique valid comments, sort by quality grade
4. Filter Top N high-quality comments
5. Output a new CSV file with columns: Review Content, Review Time, Reviewer, Quality Grade, Review Type

### Method: Docker HTTP Service

Build and run the bundled service:

```bash
docker compose up -d --build
```

Endpoints:

- `GET /`: CSV/XLSX upload page with result preview and CSV download
- `GET /healthz`: health check
- `POST /api/select`: CSV/XLSX processing API

API upload example:

```bash
curl -F "file=@reviews.csv" -F "limit=75" "http://127.0.0.1:18898/api/select" -o top_reviews.csv
```

XLSX input is also supported. The service reads the first worksheet:

```bash
curl -F "file=@reviews.xlsx" -F "limit=75" "http://127.0.0.1:18898/api/select" -o top_reviews.csv
```

The service uses deterministic grading/type heuristics by default. Set `use_ai_score=true` with `open_ai_key`, `base_url`, and optional `model` to grade comments through an OpenAI-compatible chat completions API. Selection uses all grades by `d > c > b > a`; same-grade comments are ordered by longer content first.

```bash
curl -F "file=@reviews.csv" \
  -F "limit=20" \
  -F "use_ai_score=true" \
  -F "open_ai_key=$OPENAI_API_KEY" \
  -F "base_url=https://api.openai.com/v1" \
  -F "model=gpt-4o-mini" \
  "http://127.0.0.1:18898/api/select" -o top_reviews.csv
```

Set `limit` to output Top N rows. Send `format=json` to receive metadata and selected rows as JSON.

## Output Format

Output CSV contains the following columns:
- **检视详情**: Original review comment
- **创建时间**: Retrieved from original CSV/XLSX
- **检视者**: Retrieved from original CSV/XLSX
- **质量等级**: a/b/c/d
- **检视类型**: 安全/性能/设计，等。

## Important Notes

1. **Validity Filter**: Only comments containing `review` are processed
2. **Deduplication**: Duplicate comments are removed before deterministic or AI scoring
3. **Processing Approach**: AI analyzes each comment individually for accurate assessment
4. **Encoding Format**: Defaults to UTF-8 encoding
5. **Column Mapping**: If original CSV/XLSX has different column names, AI will handle mapping automatically
6. **Quality Assessment**: AI assesses grades based on criteria, may have subjectivity
